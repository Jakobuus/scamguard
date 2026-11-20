"""Plays the scenes through /sim/call in-process. Exit non-zero on any failure."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

os.environ["FAKE"] = "1"
os.environ["TIMEOUT_S"] = "2"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from guard.app import app  # noqa: E402
from guard import settings, flow, persona, state, telegram
from guard.brain import classify, contacts  # noqa: E402

SCAM = ["Mum, it's me, I'm in trouble.", "Wire $2000 right now, don't tell dad."]
BASE = ["call.started", "transcript", "flags", "contact.lookup", "ping.sent", "decision", "action"]


def run(c, name, body, final, types):
    t0 = time.time()
    call_id = c.post("/sim/call", json=body).json()["call_id"]
    seen: set = set()
    while time.time() - t0 < 10:
        seen = {e["type"] for e in c.get("/events").json() if e["call_id"] == call_id}
        if set(types) <= seen:
            break
        time.sleep(0.1)
    missing = set(types) - seen
    assert not missing, f"{name}: missing events {missing}"
    state = c.get(f"/calls/{call_id}").json()["state"]
    assert state == final, f"{name}: state {state}, want {final}"
    print(f"OK {name}: {final} ({time.time() - t0:.1f}s)")
    return call_id, [e for e in c.get("/events").json() if e["call_id"] == call_id]


with TestClient(app) as c:  # keeps one event loop alive so timers fire
    assert c.get("/health").json()["fake"] is True
    # This fixed vector catches using the raw secret or a different derivation.
    sim_key = "b0493850cfe3ca766171"
    with patch.dict(os.environ, {"APP_SECRET": "test-app-secret"}), patch.object(settings, "FAKE", False):
        for route in ("/sim/call", "/sim/tap"):
            for kwargs in ({}, {"params": {"key": sim_key}},
                           {"params": {"key": "test-app-secret"}},
                           {"headers": {"X-Sim-Key": "test-app-secret"}},
                           {"headers": {"X-Sim-Key": "wrong"}}):
                assert c.post(route, json={"tap": "invalid"}, **kwargs).status_code == 403
            # Auth succeeds; the deliberately invalid body then fails validation.
            assert c.post(route, json={"tap": "invalid"},
                          headers={"X-Sim-Key": sim_key}).status_code == 422
        with patch.dict(os.environ, {"APP_SECRET": ""}):
            assert c.post("/sim/call", json={}, headers={"X-Sim-Key": sim_key}).status_code == 403
    for base, expected_base in (("https://demo.example/", "https://demo.example"),
                                ("", "http://localhost:8000")):
        output = subprocess.check_output(
            [sys.executable, "-m", "guard.sim"], text=True,
            env={**os.environ, "APP_SECRET": "test-app-secret", "PUBLIC_BASE_URL": base},
        )
        assert output == f"{expected_base}/#k={sim_key}\n"
    print("OK sim auth: derived header only; local page link")
    scam = {"claimed_name": "Jakob", "caller_number": "+15550999999", "lines": SCAM}
    scam_id, scam_events = run(c, "scam, tap no", {**scam, "tap": "no", "tap_after_s": 0.5}, "denied",
        BASE + ["owner.notified", "call.ended"])
    ping = next(e["data"]["text"] for e in scam_events if e["type"] == "ping.sent")
    assert "Mum" in ping and "$2000" in ping and "wire" in ping.lower()
    run(c, "real, tap yes", {"claimed_name": "Jake", "caller_number": "+15550100002",
        "lines": ["Hi, it's Jakob."], "tap": "yes", "tap_after_s": 0.5}, "confirmed", BASE)
    run(c, "scam, no answer", {**scam, "caller_number": "+15550888888"}, "unverified", BASE + ["owner.notified"])
    run(c, "credential", {"claimed_name": "Broccoli", "lines": ["I'm calling to book a slot."],
        "credential": "demo-credential"}, "agent_verified",
        ["call.started", "transcript", "credential.verified", "decision", "action"])
    known_id, known_events = run(c, "known number", {"claimed_name": "Jakob",
        "caller_number": "+15550100001", "lines": ["Hi, can we talk later?"]}, "confirmed",
        ["decision", "action"])
    assert c.get(f"/calls/{known_id}").json()["decided_by"] == "known_number"
    assert not any(e["type"] == "ping.sent" for e in known_events)
    _, relation_events = run(c, "relationship needs a name", {
        "lines": ["Mum, it's your son, I need $2000 in gift cards"]}, "screening", ["action"])
    assert any(e["data"].get("kind") == "ask_name" for e in relation_events)
    assert not any(e["type"] == "ping.sent" for e in relation_events)
    _, scope_events = run(c, "credential payment outside scope", {
        "lines": ["Please wire $2000."], "credential": "demo-credential"}, "unverified",
        ["decision", "action"])
    assert any(e["data"].get("kind") == "out_of_scope" for e in scope_events)
    assert not any(e["type"] == "credential.verified" for e in scope_events)
    # Only the same number previously denied by a tap is blocked.
    repeat_id, repeat_events = run(c, "repeat denied number", scam, "denied", ["decision", "owner.notified"])
    assert c.get(f"/calls/{repeat_id}").json()["decided_by"] == "repeat_attempt"
    assert not any(e["type"] == "ping.sent" for e in repeat_events)
    assert any("Repeat attempt" in e["data"].get("text", "") for e in repeat_events)
    assert contacts.attempts_today("jakob") == 3
    assert "+15550999999" not in contacts.get("jakob").known_numbers
    assert "+15550100002" in contacts.get("jakob").known_numbers

    async def exercise_code_words():
        def events(cid):
            return [e for e in state.events_since(0) if e.call_id == cid]

        async def matched():
            cid = flow.start_call(None)
            await flow.on_transcript(cid, "caller", "This is Jakob, please wire $2000")
            await flow.verify_caller(cid, "Jakob")
            return cid

        cid = await matched()
        await flow._resolve(cid, "unverified", "timeout")
        await flow.on_transcript(cid, "caller", "The word is BRUNO!")
        assert "bruno" not in json.dumps([e.model_dump(mode="json") for e in events(cid)]).lower()
        assert await flow.on_code_word(cid, "The word is BRUNO!") == "ok"
        assert state.get_call(cid).decided_by == "code_word"
        confirmed_events = events(cid)
        notice = next(e for e in confirmed_events if e.type == "notice.sent")
        forward = next(e for e in confirmed_events if e.data.get("kind") == "forward")
        assert notice.id > forward.id
        assert "bruno" not in json.dumps([e.model_dump(mode="json") for e in confirmed_events]).lower()
        assert "BRUNO" not in flow._caller_text[cid]
        assert await flow.on_tap(cid, "no")
        assert any(e.data.get("kind") == "late_denial" for e in events(cid))
        assert not await flow.on_tap(cid, "no")

        cid = await matched()
        assert await flow.on_code_word(cid, "Brunometer") == "retry"
        assert await flow.on_code_word(cid, "wrong") == "failed"
        assert state.get_call(cid).state == "unverified"
        assert any(e.data.get("kind") == "hangup" for e in events(cid))
        assert any(e.type == "owner.notified" and "failed the code word" in e.data["text"] for e in events(cid))
        assert await flow.on_code_word(cid, "Bruno") == "failed"

        cid = await matched()
        await flow._resolve(cid, "unverified", "timeout")
        assert await flow.on_code_word(cid, "wrong") == "retry"
        assert await flow.on_code_word(cid, "still wrong") == "failed"
        assert state.get_call(cid).state == "unverified"
        assert any(e.data.get("kind") == "code_word_failed" for e in events(cid))

        cid = await matched()
        await flow.on_tap(cid, "no")
        before = len(events(cid))
        assert await flow.on_code_word(cid, "Bruno") == "failed"
        assert state.get_call(cid).state == "denied" and len(events(cid)) == before

        flow._pings.clear()
        cid = await matched()
        assert state.get_call(cid).state == "pending"
        assert await flow.on_code_word(cid, " bruno. ") == "ok"
        assert not await flow._resolve(cid, "unverified", "timeout")
        assert await flow.on_tap(cid, "no")
        assert state.get_call(cid).decided_by == "code_word"
        cid = flow.start_call(None)
        state.set_pending(cid, None)
        assert await flow.on_code_word(cid, "Bruno") == "failed"
        assert state.get_call(cid).state == "pending"
        cid = flow.start_call(None)
        await flow.verify_caller(cid, "Unknown")
        assert await flow.on_code_word(cid, "Bruno") == "failed"

    with patch.object(flow, "_pings", {}), patch.object(contacts, "_attempts", []):
        c.portal.call(exercise_code_words)
    print("OK code words: timeout recovery, redaction, two tries, denial, pending winner")

    async def exercise_ping_cap():
        ids = []
        for i in range(7):
            cid = flow.start_call(None)
            await flow.on_transcript(cid, "caller", "This is Jakob, please wire $2000")
            await flow.verify_caller(cid, "Jakob")
            ids.append(cid)
        evs = [e for e in state.events_since(0) if e.call_id in ids]
        assert len([e for e in evs if e.type == "ping.sent"]) == 5
        for cid in ids[5:]:
            assert state.get_call(cid).state == "unverified"
            assert state.get_call(cid).decided_by == "ping_cap"
        assert len([e for e in evs if e.type == "owner.notified"]) == 1
        assert await flow.on_code_word(ids[5], "Bruno") == "ok"
        other = flow.start_call(None)
        await flow.verify_caller(other, "Lena")
        assert state.get_call(other).state == "pending"
        # Tomorrow starts a fresh budget without clearing the process counters.
        from datetime import datetime, timedelta, timezone
        tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
        with patch("guard.flow.datetime") as clock:
            clock.now.return_value = tomorrow
            fresh = flow.start_call(None)
            await flow.verify_caller(fresh, "Jakob")
            assert state.get_call(fresh).state == "pending"

    with patch.object(flow, "_pings", {}), patch.object(flow, "_cap_notified", set()):
        c.portal.call(exercise_ping_cap)
    print("OK ping cap: five per contact per UTC day, one warning, code word still works")

    # Real tool payload shapes and auth, with no expected answer in a response.
    async def pending_tool_call():
        cid = flow.start_call(None)
        state.set_pending(cid, "jakob")
        return cid

    cid = c.portal.call(pending_tool_call)
    with patch.dict(os.environ, {"VAPI_SERVER_SECRET": "tool-test-secret"}):
        body = {"message": {"call": {"id": cid}, "toolCallList": [{
            "id": "word-1", "function": {"arguments": json.dumps({"spoken": "wrong"})}}]}}
        assert c.post("/vapi/tool/check_code_word", json=body).status_code == 401
        result = c.post("/vapi/tool/check_code_word", json=body,
                        headers={"X-Vapi-Secret": "tool-test-secret"}).json()
        assert result == {"results": [{"toolCallId": "word-1", "result": "That is not right. Ask once more."}]}
        body["message"]["toolCallList"][0]["function"]["arguments"] = {"spoken": "BRUNO!"}
        result = c.post("/vapi/tool/check_code_word", json=body,
                        headers={"X-Vapi-Secret": "tool-test-secret"}).json()
        assert result["results"][0]["result"] == "Verified. Tell the caller you are putting them through."
        assert "bruno" not in json.dumps(result).lower()
        # Flat payload and malformed arguments fail closed.
        result = c.post("/vapi/tool/check_code_word", json={"call": {"id": cid}, "spoken": "wrong"},
                        headers={"X-Vapi-Secret": "tool-test-secret"}).json()
        assert result["results"][0]["result"].startswith("Not verified.")
        bad = {"call": {"id": cid}, "toolCalls": [{"arguments": "invalid json"}]}
        assert c.post("/vapi/tool/check_code_word", json=bad,
                      headers={"X-Vapi-Secret": "tool-test-secret"}).status_code == 200
    print("OK code word tool: auth, JSON and object arguments, safe results")

    # Exercise allowed transitions in both backends; SQL checks its atomic predicate.
    from guard.contracts import Call
    from datetime import datetime, timezone
    for backend in (state.MemoryBackend(), state.SingleStoreBackend()):
        for old, want in (("pending", True), ("unverified", True), ("denied", False), ("confirmed", False)):
            backend.calls["transition"] = Call(call_id="transition", state=old, contact_id="jakob",
                                              started_at=datetime.now(timezone.utc))
            if isinstance(backend, state.SingleStoreBackend):
                def execute(sql, *args):
                    assert "WHERE call_id=? AND state IN ('pending','unverified')" in sql
                    assert args == ("confirmed", "code_word", "transition")
                    return int(old in ("pending", "unverified"))
                backend.available = True
                with patch.object(backend, "_exec", execute), patch.object(backend, "get_call", return_value=backend.calls["transition"]):
                    assert backend.decide("transition", "confirmed", "code_word") == want
            else:
                assert backend.decide("transition", "confirmed", "code_word") == want
    print("OK code word states: memory and atomic SQL permit pending/unverified only")

    # Local entries can override just private fields and extend the demo circle.
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory) / "contacts.json"
        base.write_text(Path("data/contacts.json").read_text())
        extra = {"id": "new", "name": "New", "relationship": "friend",
                 "challenge_question": "What is the family code word?", "challenge_answer": "test-word"}
        base.with_name("contacts.local.json").write_text(json.dumps([
            {"id": "jakob", "challenge_answer": "private-test", "telegram_chat_id": "test-chat"}, extra]))
        with patch.object(contacts, "_PATH", base), patch.object(contacts, "_contacts", None):
            assert contacts.get("jakob").name == "Jakob"
            assert contacts.get("jakob").challenge_answer == "private-test"
            assert contacts.get("jakob").telegram_chat_id == "test-chat"
            assert contacts.get("new").relationship == "friend"
    print("OK local contacts: partial override and extension by id")

    async def exercise_trust_gaps():
        from guard.contracts import Call
        from datetime import datetime, timedelta, timezone

        def events(cid):
            return [e for e in state.events_since(0) if e.call_id == cid]

        async def matched(number, text="This is Jakob, please wire $2000"):
            cid = flow.start_call(number)
            if text is not None:
                await flow.on_transcript(cid, "caller", text)
            await flow.verify_caller(cid, "Jakob")
            return cid

        with patch.object(contacts, "_attempts", []), patch.object(flow, "_pings", {}):
            cid = await matched("+15550100001", None)
            assert state.get_call(cid).state == "pending", "missing flags must require verification"
            await flow.on_tap(cid, "yes")
            cid = await matched("+15550100001", "Hello, can we talk later?")
            assert state.get_call(cid).decided_by == "known_number"
            notice = next(e for e in events(cid) if e.type == "notice.sent")
            assert notice.data["text"] == "You are calling Mum right now. Not you? Tap below."
            assert not await flow.on_tap(cid, "yes")
            assert await flow.on_tap(cid, "no")
            assert state.get_call(cid).state == "confirmed"
            assert state.get_call(cid).decided_by == "known_number"
            assert any(e.data.get("kind") == "late_denial" for e in events(cid))
            assert any(e.data.get("kind") == "hangup" for e in events(cid))
            assert any(e.type == "owner.notified" and e.data["text"] ==
                       "That call was not Jakob. Don't send money." for e in events(cid))
            assert not await flow.on_tap(cid, "no")

        sent = []
        def capture(method, payload):
            sent.append((method, payload))
        with patch.object(settings, "REAL_TELEGRAM", True), patch.object(telegram, "_api", capture), patch.dict(os.environ, {"APP_SECRET": "notice-test-secret", "TELEGRAM_CHAT_ID": "123"}):
            telegram.send_notice(state.get_call(cid), contacts.get("jakob"))
        button, = sent[0][1]["reply_markup"]["inline_keyboard"][0]
        assert button["text"] == "Not me"
        with patch.dict(os.environ, {"APP_SECRET": "notice-test-secret"}):
            assert telegram.verify_callback(button["callback_data"]) == ("n", cid)

        with patch.object(contacts, "_attempts", []), patch.object(flow, "_pings", {}):
            for number, count in (("+15550770001", 1), ("+15550770001", 2), ("+15550770001", 3), ("+15550770002", 4)):
                cid = await matched(number)
                assert state.get_call(cid).state == "pending"
                text = next(e.data["text"] for e in events(cid) if e.type == "ping.sent")
                if count > 1:
                    assert f" (attempt {count} today)" in text
                if count < 4:
                    await flow._resolve(cid, "unverified", "timeout")
                else:
                    await flow.on_tap(cid, "no")
            sent.clear()
            with patch.object(settings, "REAL_TELEGRAM", True), patch.object(telegram, "_api", capture), patch.dict(os.environ, {"APP_SECRET": "notice-test-secret", "TELEGRAM_CHAT_ID": "123"}):
                telegram.send_ping(state.get_call(cid), contacts.get("jakob"))
            assert " (attempt 5 today)" in sent[0][1]["text"]
            denied = state.get_call(cid)
            blocked = await matched("+15550770002")
            assert state.get_call(blocked).decided_by == "repeat_attempt"
            assert not any(e.type == "ping.sent" for e in events(blocked))
            other_contact = flow.start_call("+15550770002")
            await flow.on_transcript(other_contact, "caller", "This is Lena, please wire $2000")
            await flow.verify_caller(other_contact, "Lena")
            assert state.get_call(other_contact).state == "pending"
            await flow.on_tap(other_contact, "yes")
            real = await matched("+15550770003")
            assert state.get_call(real).state == "pending"
            assert contacts.attempts_today("jakob", "+15550770001") == 3
            assert contacts.attempts_today("jakob", "+15550770003") == 0
            assert await flow.on_tap(real, "yes")
            contacts._attempts[:] = [(datetime.now(timezone.utc) - timedelta(days=1), denied)]
            flow._pings.clear()  # The attempts fixture above has moved to a new day.
            fresh = await matched("+15550770002")
            assert state.get_call(fresh).state == "pending"
            await flow.on_tap(fresh, "yes")

        with patch.object(contacts, "_attempts", []), patch.object(flow, "_pings", {}):
            for fake, real_persona, failure, expected, by in (
                (False, True, None, "unverified", "stepup_unavailable"),
                (False, True, RuntimeError("offline"), "unverified", "stepup_unavailable"),
                (False, False, None, "confirmed", "tap"),
                (True, True, None, "confirmed", "tap"),
            ):
                cid = await matched(None)
                with patch.object(settings, "FAKE", fake), patch.object(settings, "REAL_PERSONA", real_persona), patch.object(persona, "start", return_value=None, side_effect=failure):
                    assert await flow.on_tap(cid, "yes")
                assert state.get_call(cid).state == expected
                assert state.get_call(cid).decided_by == by
                assert not await flow.on_tap(cid, "yes")
            cid = await matched(None, "Hello")
            await flow.on_transcript(cid, "caller", "This is Jakob, please wire $2000")
            with patch.object(settings, "FAKE", False), patch.object(settings, "REAL_PERSONA", True), patch.object(persona, "start", return_value=None):
                assert await flow.on_tap(cid, "yes")
            assert state.get_call(cid).decided_by == "stepup_unavailable"

        contact = contacts.get("jakob")
        with patch.object(contact, "known_numbers", []):
            for by, number in (("known_number", "+15550660001"), ("other", "+15550660002"), ("tap", "+15550660003"), ("stepup", "+15550660004")):
                contacts.learn(Call(call_id=number, state="confirmed", contact_id="jakob", caller_number=number, started_at=datetime.now(timezone.utc), decided_by=by))
            assert contact.known_numbers == ["+15550660003", "+15550660004"]

    c.portal.call(exercise_trust_gaps)
    preview = json.loads(subprocess.check_output(
        [sys.executable, "scripts/vapi_setup.py", "--dry-run"], text=True,
        env={**os.environ, "OWNER_NAME": "Alex"},
    ))
    assert "Alex's assistant" in preview["firstMessage"]
    assert "Alex" in preview["model"]["messages"][0]["content"]
    tools = {tool["function"]["name"]: tool for tool in preview["model"]["tools"]}
    assert set(tools) == {"verify_caller", "check_code_word"}
    assert tools["check_code_word"]["function"]["parameters"]["required"] == ["spoken"]
    assert tools["check_code_word"]["server"]["url"].endswith("/vapi/tool/check_code_word")
    print("OK trust gaps: notice, late denial, repeat numbers, Persona failure, safe learning, setup preview")

    async def exercise_seams():
        from guard.contracts import Claim

        claim = classify.claim("Mum, it's your son, I need $2000 in gift cards")
        assert claim.claimed_name is None and claim.relationship == "son"
        assert claim.amount == "$2000" and claim.method == "gift cards" and claim.quote
        assert classify.flags(claim.quote).needs_verification
        assert not classify.flags("Please wire $2000").needs_verification
        assert contacts.lookup("Jake", None).contact.id == "jakob"
        assert contacts.candidates(Claim(relationship="daughter"), None)[0].contact.id == "lena"
        # A known number still requires a tap for a sensitive identity claim.
        cid = flow.start_call("+15550100003")
        await flow.on_transcript(cid, "caller", "Mum, this is Lena, please wire $2000")
        await flow.verify_caller(cid, None)
        assert state.get_call(cid).state == "pending"
        assert any(e.call_id == cid and e.type == "ping.sent" for e in state.events_since(0))
        assert await flow.on_tap(cid, "yes")
        cid = flow.start_call(None)
        await flow.on_transcript(cid, "caller", claim.quote)
        await flow.verify_caller(cid, None)
        await flow.verify_caller(cid, None)
        assert state.get_call(cid).state == "unverified"
        assert not any(e.call_id == cid and e.type == "ping.sent" for e in state.events_since(0))
        cid = flow.start_call(None)
        await flow.on_transcript(cid, "caller", "It's your daughter, I need $2000 in gift cards")
        await flow.verify_caller(cid, None)
        await flow.verify_caller(cid, "Lena")
        assert state.get_call(cid).state == "pending"
        with patch.object(persona, "start", return_value="https://verify.example/check"):
            assert await flow.on_tap(cid, "yes")
            assert state.get_call(cid).state == "pending"
            assert not await flow.on_tap(cid, "yes")
            assert any(e.call_id == cid and e.data.get("kind") == "stepup"
                       and e.data.get("link") == "https://verify.example/check" for e in state.events_since(0))
            assert await flow.on_stepup(cid, True)
            assert state.get_call(cid).decided_by == "stepup"
            assert not await flow.on_stepup(cid, False)
        cid = flow.start_call(None)
        await flow.on_transcript(cid, "caller", "This is Lena, please wire $2000")
        await flow.verify_caller(cid, None)
        with patch.object(persona, "start", return_value="https://verify.example/check"):
            assert await flow.on_tap(cid, "yes")
            assert await flow.on_stepup(cid, False)
            assert state.get_call(cid).state == "denied"
            assert not await flow.on_tap(cid, "yes")
        seen = []
        def broken_subscriber(event):
            raise RuntimeError("subscriber is unavailable")
        state.subscribe(broken_subscriber)
        state.subscribe(seen.append)
        with patch("guard.state.logging.getLogger"):
            event = state.emit(cid, "action", {"kind": "say", "text": "Still holding."})
        assert seen == [event]
        state._subscribers.remove(broken_subscriber)
        state._subscribers.remove(seen.append)
    with patch.object(flow, "_pings", {}):
        c.portal.call(exercise_seams)
    print("OK seams: claim extraction, learning, repeat limit, name retry, step-up, subscribers")
print("ALL OK")
