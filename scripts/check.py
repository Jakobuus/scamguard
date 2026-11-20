"""Plays the scenes through /sim/call in-process. Exit non-zero on any failure."""
import faulthandler
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

os.environ["FAKE"] = "1"
os.environ["TIMEOUT_S"] = "2"
os.environ["SIM_WORD_S"] = "0"  # scenes skip the word-by-word delay; the live check sets its own
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from guard.app import app  # noqa: E402
from guard import channels, settings, flow, persona, state, telegram
from guard.brain import classify, contacts, scripts  # noqa: E402

_last = ["start (nothing finished yet)"]


def _watchdog():
    # Fail fast: a hung check must say where it was and exit non-zero, never sit forever.
    sys.stderr.write(f"\nWATCHDOG: check exceeded 90 s; last finished scene: {_last[0]}\n")
    faulthandler.dump_traceback(all_threads=True)
    sys.stderr.flush()
    os._exit(1)


_timer = threading.Timer(90, _watchdog)
_timer.daemon = True
_timer.start()
faulthandler.enable()


def ok(msg):
    _last[0] = msg
    print("OK " + msg, flush=True)


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
    ok(f"{name}: {final} ({time.time() - t0:.1f}s)")
    return call_id, [e for e in c.get("/events").json() if e["call_id"] == call_id]


with TestClient(app) as c:  # keeps one event loop alive so timers fire
    assert c.get("/health").json()["fake"] is True
    assert c.get("/health").json()["services"]["beeper"] == "fake"
    assert c.get("/health").json()["services"]["email"] == "fake"
    assert c.get("/health").json()["owner_name"] == settings.OWNER_NAME
    # Missing fan-out or a channel bypassing the atomic decision must fail these checks.
    async def channel_question():
        flow.reset_demo()
        cid = flow.start_call(None)
        await flow.on_transcript(cid, "caller", "This is Lena, please wire $2000")
        await flow.verify_caller(cid, "Lena")
        return cid

    with patch.dict(os.environ, {"REAL_BEEPER": "1", "REAL_EMAIL": "1",
                               "BEEPER_ACCESS_TOKEN": "test", "BEEPER_CHAT_ID": "test-chat",
                               "RESEND_API_KEY": "test", "PING_EMAIL_TO": "test@example.invalid",
                               "PUBLIC_BASE_URL": "https://guard.example", "APP_SECRET": "channel-test"}):
        cid = c.portal.call(channel_question)
        ping = next(e.data for e in state.events_since(0) if e.call_id == cid and e.type == "ping.sent")
        assert ping["channels"] == ["telegram", "whatsapp", "email"]
        assert c.portal.call(flow.on_tap, cid, "yes", "email")
        assert not c.portal.call(flow.on_tap, cid, "no", "whatsapp")
        decision = next(e.data for e in state.events_since(0) if e.call_id == cid and e.type == "decision")
        assert decision["state"] == "confirmed" and decision["channel"] == "email"
        cid = c.portal.call(channel_question)
        signed = telegram.callback_data("n", cid)
        page = c.get("/answer", params={"d": signed})
        assert page.status_code == 200 and 'method="post"' in page.text
        assert state.get_call(cid).state == "pending"
        assert "Call ended" in c.post("/answer", data={"d": signed}).text
        assert state.get_call(cid).state == "denied"
        assert "already decided" in c.post("/answer", data={"d": signed}).text
        assert "no longer valid" in c.get("/answer", params={"d": signed + "bad"}).text
        assert "no longer valid" in c.post("/answer", data={"d": signed + "bad"}).text
    flow.reset_demo()
    ok("channels: fan-out, first answer wins, scanner-safe email confirmation")
    async def channel_transports():
        import asyncio
        import httpx
        from datetime import datetime, timedelta, timezone
        from urllib.parse import parse_qs, urlparse
        import re
        import html

        for word in ("NO", "n", "nope please", "nein"):
            assert channels.reply_answer(word) == "no"
        for word in ("Yes please", "y", "JA"):
            assert channels.reply_answer(word) == "yes"
        assert channels.reply_answer("yesterday") is None
        assert channels.reply_answer("") is None
        assert channels.enabled() == ["telegram"]

        with patch.dict(os.environ, {"RESEND_API_KEY": "test", "PING_EMAIL_TO": "test@example.invalid",
                                   "PUBLIC_BASE_URL": "https://guard.example", "APP_SECRET": "channel-test",
                                   "BEEPER_ACCESS_TOKEN": "test", "BEEPER_CHAT_ID": "chat/id"}):
            cid = await channel_question()
            call = state.get_call(cid)
            text = "Someone is calling. Is this you?"
            requests = []
            def email_http(request):
                requests.append(request)
                return httpx.Response(200, json={"id": "test-email"})
            with httpx.Client(transport=httpx.MockTransport(email_http)) as client:
                with patch.object(settings, "REAL_EMAIL", True), patch.object(channels.httpx, "post", client.post):
                    await asyncio.to_thread(channels._email, call, text)
            req, = requests
            assert str(req.url) == "https://api.resend.com/emails"
            assert req.headers["user-agent"] == "curl/8.7.1"
            assert req.headers["authorization"] == "Bearer test"
            assert req.extensions["timeout"]["read"] <= 3
            body = json.loads(req.content)
            assert body["subject"] == text and body["to"] == ["test@example.invalid"]
            links = re.findall('href="([^"]+)"', body["html"])
            assert len(links) == 2
            for link, action in zip(links, ("n", "y")):
                d = parse_qs(urlparse(html.unescape(link)).query)["d"][0]
                assert telegram.verify_callback(d) == (action, cid)

            now = datetime.now(timezone.utc)
            sent = []
            def beeper_http(request):
                assert request.url.path == "/v1/chats/chat/id/messages"
                assert request.headers["authorization"] == "Bearer test"
                if request.method == "POST":
                    sent.append(json.loads(request.content)["text"])
                    # Real shape: the pending id differs from the listed id.
                    return httpx.Response(200, json={"chatID": "chat/id", "pendingMessageID": "~pending_1_8"})
                def msg(mid, ts, body):
                    return {"id": mid, "timestamp": ts, "text": body, "type": "TEXT", "isSender": True}
                items = [msg("100", "2020-01-01T00:00:00.000Z", "no")]
                if sent:  # newest first; listed text has <br>, the reply's second stamp is EARLIER than the question's
                    q = (now + timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%S.") + "681Z"
                    r = (now + timedelta(seconds=4)).strftime("%Y-%m-%dT%H:%M:%S.") + "000Z"
                    items = [msg("103", r, "JA please"), msg("102", q, "hello"),
                             msg("101", q, sent[0].replace(" Reply NO", "<br>Reply NO"))] + items
                    if len(sent) > 1:
                        items.insert(0, msg("104", q, sent[1]))
                return httpx.Response(200, json={"hasMore": False, "items": items,
                                                 "newestCursor": "new", "oldestCursor": "old"})
            with httpx.Client(transport=httpx.MockTransport(beeper_http)) as client:
                with patch.object(settings, "REAL_BEEPER", True), patch.object(channels.httpx, "request", client.request):
                    await channels._whatsapp(call, text)
            assert sent == [text + " Reply NO or YES.", "Thanks. Putting the call through."]
            assert state.get_call(cid).state == "confirmed"
            decision = next(e.data for e in state.events_since(0) if e.call_id == cid and e.type == "decision")
            assert decision["channel"] == "whatsapp"
            for t, want in (("No.", "no"), ("no!", "no"), ("Yeah", "yes"), ("\U0001F44E", "no"), ("maybe", None),
                            ("Thanks. Call ended.", None), (text + "<br>Reply NO or YES.", None), (None, None)):
                assert channels.reply_answer(t) == want, t
            # A poller whose call is no longer the latest for its chat stops without answering.
            flow.reset_demo()
            cid2 = await channel_question()
            call2 = state.get_call(cid2)
            taps = []
            async def fake_tap(*a, **k):
                taps.append(a)
                return True
            channels._latest["chat/id"] = "someone-else"
            real_request = channels.beeper_request
            with httpx.Client(transport=httpx.MockTransport(beeper_http)) as client:
                with patch.object(settings, "REAL_BEEPER", True), patch.object(channels.httpx, "request", client.request), \
                     patch.object(flow, "on_tap", fake_tap):
                    stale = asyncio.create_task(channels._whatsapp(call2, text))
                    await asyncio.sleep(0.05)
                    channels._latest["chat/id"] = "newer-call"
                    await asyncio.wait_for(stale, 3)
            assert taps == []
        flow.reset_demo()
        # A stalled delivery must neither delay Telegram nor prevent the winning tap.
        import threading
        gate, started, telegram_sent = threading.Event(), threading.Event(), threading.Event()
        def stalled_email(*args):
            started.set()
            gate.wait(3)
            raise RuntimeError("private HTTP error must not be logged")
        def telegram_delivery(*args):
            telegram_sent.set()
        try:
            with patch.dict(os.environ, {"REAL_EMAIL": "1", "RESEND_API_KEY": "test",
                                       "PING_EMAIL_TO": "test@example.invalid", "PUBLIC_BASE_URL": "https://guard.example",
                                       "APP_SECRET": "channel-test"}), patch.object(channels, "_email", stalled_email), patch.object(telegram, "send_ping", telegram_delivery):
                cid = await channel_question()
                assert await asyncio.to_thread(started.wait, 1)
                assert await asyncio.to_thread(telegram_sent.wait, 1)
                assert await flow.on_tap(cid, "yes", channel="telegram")
                assert state.get_call(cid).state == "confirmed"
        finally:
            gate.set()
        await asyncio.sleep(0.05)
        flow.reset_demo()
    c.portal.call(channel_transports)
    ok("channels: Resend payload and signed links, Beeper requests and own-account replies")
    async def audience_claims():
        for method, display in (("gift cards", "$2,000 in gift cards"), ("wire", "$2,000 by wire")):
            cid = flow.start_call("+15550990001")
            await flow.on_transcript(cid, "caller", f"This is Jakob, I need $2000 in {method}")
            await flow.verify_caller(cid, None)
            evs = [e for e in state.events_since(0) if e.call_id == cid]
            assert next(e.data for e in evs if e.type == "claim")["asks_for"] == display
            assert next(e.data for e in evs if e.type == "ping.sent")["timeout_s"] == settings.TIMEOUT_S
            await flow.on_tap(cid, "no")
            if method == "gift cards":
                flow.reset_demo()
    c.portal.call(audience_claims)
    before_reset = c.get("/events").json()
    assert c.post("/sim/reset").status_code == 200
    assert c.get("/events").json() == before_reset
    _, reset_events = run(c, "Reset permits denied number", {
        "caller_number": "+15550990001", "lines": ["This is Jakob, wire $2000"], "tap": "no"},
        "denied", BASE)
    assert any(e["type"] == "ping.sent" for e in reset_events)
    assert c.post("/sim/reset").status_code == 200
    ok("audience claims, timeout, health and silent demo reset")
    # This fixed vector catches using the raw secret or a different derivation.
    sim_key = "b0493850cfe3ca766171"
    with patch.dict(os.environ, {"APP_SECRET": "test-app-secret"}), patch.object(settings, "FAKE", False):
        assert c.post("/sim/reset").status_code == 403
        assert c.post("/sim/reset", headers={"X-Sim-Key": sim_key}).status_code == 200
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
            env={**os.environ, "APP_SECRET": "test-app-secret", "PUBLIC_BASE_URL": base}, timeout=30,
        )
        assert output == f"{expected_base}/#k={sim_key}\n"
    ok("sim auth: derived header only; local page link")
    # Finals alone must resolve specific expectations without an identity tool call.
    async def expected_without_tool():
        import asyncio

        async def scene(lines, decision=None):
            cid = flow.start_call(None)
            for line in lines:
                await flow.on_transcript(cid, "caller", line)
            await asyncio.sleep(0.9)
            call = state.get_call(cid)
            evs = [e for e in state.events_since(0) if e.call_id == cid]
            assert not any(e.type == "claim" for e in evs)
            assert call.decided_by == decision, (lines, call)
            assert any(e.data.get("kind") == "forward" for e in evs) == (decision == "expected")
            if decision == "expected_but_money":
                assert any(e.type == "owner.notified" and "Don't pay" in e.data["text"] for e in evs)
            return cid

        opening = "Hi, I've got a parcel for you, the desk lamp order, I'm at the door"
        await scene([opening], "expected")
        await scene([opening + ", hurry, don't tell anyone"], "lookup")
        for request in ("There is a fee to pay", "What is your card number?"):
            await scene([opening, request], "expected_but_money")
        for words in ("I've got a parcel for you", "it's about your order",
                      "This is Jakob with your desk lamp parcel"):
            await scene([words])
        cid = flow.start_call(None)
        await flow.on_transcript(cid, "caller", "Hello")
        await flow.verify_caller(cid, None)
        await flow.on_transcript(cid, "caller", opening)
        await asyncio.sleep(0.9)
        assert state.get_call(cid).decided_by == "lookup"

    c.portal.call(expected_without_tool)
    ok("expected finals: no name or tool needed; money, vague claims and circle claims get no shortcut")
    for name, reason, late, decision in (
        ("", "I am the courier with your desk lamp parcel", None, "expected"),
        ("", "I am the courier with your desk lamp parcel", "There is a customs fee, what's your card number", "expected_but_money"),
        ("", "Your desk lamp parcel requires a fee and card number", None, "expected_but_money"),
        ("Jakob", "Desk lamp parcel", None, None),
    ):
        cid = flow.start_call(None)
        reply = c.post("/vapi/tool/verify_caller", json={"message": {"call": {"id": cid},
            "toolCalls": [{"id": "delivery", "function": {"arguments": {
                "claimed_name": name, "reason": reason}}}]}})
        assert reply.status_code == 200
        if late:
            time.sleep(0.3)  # lands inside the wait, like a final transcript after the tool summary
            c.portal.call(flow.on_transcript, cid, "caller", late)
        deadline = time.time() + 3
        while state.get_call(cid).state == "screening" and time.time() < deadline:
            time.sleep(0.02)
        assert state.get_call(cid).decided_by == decision, (reason, late, state.get_call(cid))
        if name:
            assert state.get_call(cid).state == "pending", "a circle claim must still ask the person"
            c.portal.call(flow.on_tap, cid, "yes")
    ok("expected tool: money within the wait blocks the shortcut; a clean courier line is put through; circle claims still ask")
    for reason, flagged, decision in (("package", False, "lookup"), ("package, there is a fee to pay, card number", True, "lookup")):
        cid = flow.start_call(None)
        c.portal.call(flow.verify_caller, cid, "FedEx", reason)
        call = state.get_call(cid)
        evs = [e for e in state.events_since(0) if e.call_id == cid]
        assert call.decided_by == decision, (reason, call)
        took = any(e.data.get("kind") == "take_message" for e in evs)
        if flagged:
            assert not took and not any(e.data.get("kind") == "forward" for e in evs)
            assert any(e.type == "owner.notified" and "Don't send money" in e.data["text"] for e in evs), evs
        else:
            assert call.state == "unverified" and took
            assert any(e.type == "owner.notified" and "delivery or appointment" in e.data["text"] for e in evs)
    ok("courier naming no item: message taken; with a fee request no shortcut and a warning")
    delivery_id, delivery_events = run(c, "Delivery driver, expected", {
        "caller_number": "+15550440001",
        "lines": ["Hi, I'm the driver with your desk lamp, are you home?"]}, "confirmed",
        ["context.expected", "decision", "action", "owner.notified"])
    assert c.get(f"/calls/{delivery_id}").json()["decided_by"] == "expected"
    assert next(e for e in delivery_events if e["type"] == "contact.lookup")["data"]["note"] == "Not someone from the circle."
    assert [e["type"] for e in delivery_events].index("contact.lookup") < [e["type"] for e in delivery_events].index("context.expected")
    assert not any(e["type"] == "ping.sent" for e in delivery_events)
    assert any(e["data"].get("kind") == "forward" for e in delivery_events)
    assert any("desk lamp" in e["data"].get("text", "") for e in delivery_events if e["type"] == "owner.notified")
    fee_partials = []
    original_partial = flow.on_partial
    def capture_fee_partial(call_id, role, text):
        original_partial(call_id, role, text)
        live = flow.live()  # words only: "ts" is a float that can contain "1111" by chance
        fee_partials.append({r: (live[r] or {}).get("text") for r in ("caller", "agent")})
    with patch.object(flow, "on_partial", side_effect=capture_fee_partial):
        fee_id, fee_events = run(c, "Fake delivery fee", {
            "caller_number": "+15550440002",
            "lines": ["I'm calling from the parcel service about your desk lamp delivery.",
                      "There is a small customs fee of $4.99 to pay. What is your card number?",
                      "I'll process that payment now."],
            "agent_lines": {"0": "Who is calling, and what is it about?",
                            "2": "My card number is 4111 1111 1111 1111."},
            "word_s": 0.001}, "unverified", ["decision", "action", "owner.notified"])
        # The transcript safety net can decide before the assistant finishes speaking.
        deadline = time.time() + 3
        while time.time() < deadline:
            fee_events = [e for e in c.get("/events").json() if e["call_id"] == fee_id]
            if any("[card number]" in e["data"].get("text", "") for e in fee_events if e["type"] == "transcript"):
                break
            time.sleep(0.05)
    assert any(e["type"] == "context.expected" for e in fee_events)
    assert c.get(f"/calls/{fee_id}").json()["decided_by"] == "expected_but_money"
    assert any(e["data"].get("kind") == "expected_but_money" for e in fee_events)
    assert not any(e["data"].get("kind") == "forward" for e in fee_events)
    assert any("Don't pay" in e["data"].get("text", "") for e in fee_events if e["type"] == "owner.notified")
    assert all("4111" not in json.dumps(e) and "1111" not in json.dumps(e) for e in fee_events)
    assert any("[card number]" in e["data"].get("text", "") for e in fee_events if e["type"] == "transcript")
    partial_texts = [part[role]["text"] for part in fee_partials
                     for role in ("caller", "agent") if part[role]]
    assert all("4111" not in text and "1111" not in text for text in partial_texts), "fee scene leaked card digits live"
    assert any("[card number]" in text for text in partial_texts)
    ok("fee scene: card masked live and in final events, no expected-call shortcut, owner warned")
    generic_id, generic_events = run(c, "Generic delivery takes a message", {
        "lines": ["I'm a driver with a delivery, are you home?"]}, "unverified",
        ["decision", "action", "owner.notified"])
    assert any(e["data"].get("kind") == "take_message" for e in generic_events)
    assert not any(e["data"].get("kind") == "forward" for e in generic_events)
    cash_id, cash_events = run(c, "Expected delivery asks for cash", {
        "lines": ["driver with your desk lamp, I need 40 dollars cash right now"]}, "unverified",
        ["decision", "action", "owner.notified"])
    assert c.get(f"/calls/{cash_id}").json()["decided_by"] == "expected_but_money"
    assert not any(e["data"].get("kind") == "forward" for e in cash_events)
    _, pressure_events = run(c, "Expected delivery with pressure", {
        "lines": ["driver with your desk lamp, hurry, I leave in one minute, don't tell anyone"]}, "unverified",
        ["decision", "action", "owner.notified"])
    assert any(e["data"].get("kind") == "take_message" for e in pressure_events)
    assert not any(e["data"].get("kind") == "forward" for e in pressure_events)
    async def contact_money():
        cid = flow.start_call(None)
        await flow.on_transcript(cid, "caller", "This is Jakob, pay for my desk lamp parcel with gift cards")
        await flow.verify_caller(cid, None)
        events = [e for e in state.events_since(0) if e.call_id == cid]
        assert state.get_call(cid).state == "pending"
        assert not any(e.type == "context.expected" for e in events)
        assert not any(e.data.get("kind") == "forward" for e in events)
        await flow.on_tap(cid, "yes")
    with patch.object(flow, "_pings", {}):
        c.portal.call(contact_money)
    async def expected_safety():
        from guard.contracts import Flags
        for text in ("Desk lamp parcel requires a fee", "Desk lamp driver needs payment details",
                     *(f"Desk lamp driver requests {word}" for word in (
                         "wire", "transfer", "cash", "crypto", "bitcoin", "dollars", "$40",
                         "customs", "deposit", "card number"))):
            cid = flow.start_call(None)
            await flow.on_transcript(cid, "caller", text)
            state.update_call(cid, flags=Flags())  # Wording blocks even if the classifier misses it.
            await flow.verify_caller(cid, None)
            assert state.get_call(cid).decided_by == "expected_but_money"
            assert not any(e.call_id == cid and e.data.get("kind") == "forward" for e in state.events_since(0))
        # Each risk flag independently blocks; missing flags also fail closed.
        for flags in (None, Flags(urgency=True), Flags(secrecy=True), Flags(needs_verification=True)):
            cid = flow.start_call(None)
            await flow.on_transcript(cid, "caller", "Desk lamp driver")
            state.update_call(cid, flags=flags)
            await flow.verify_caller(cid, None)
            events = [e for e in state.events_since(0) if e.call_id == cid]
            assert state.get_call(cid).state == "unverified"
            assert any(e.data.get("kind") == "take_message" for e in events)
            assert any(e.data.get("kind") == "challenge" and "short message" in e.data.get("text", "") for e in events)
            assert not any(e.data.get("kind") in ("forward", "expected_but_money") for e in events)
            assert any(e.type == "owner.notified" and e.data["text"] ==
                       "A caller mentioned your parcel service for the desk lamp order arriving today. "
                       "I took a message instead of putting them through." for e in events)
            assert not any(e.type == "owner.notified" and "money" in e.data["text"] for e in events)
        cid = flow.start_call(None)
        await flow.on_transcript(cid, "caller", "Desk lamp driver")
        def flags_changed(text):
            state.update_call(cid, flags=Flags(money=True))
            return original_expected(text)
        original_expected = flow.context.expected
        with patch.object(flow.context, "expected", side_effect=flags_changed):
            await flow.verify_caller(cid, None)
        assert state.get_call(cid).decided_by == "expected_but_money"
        # The same flow starts in screening on either state backend. The SQL
        # transport is simulated here; no live database is needed for checks.
        for backend in (state.MemoryBackend(), state.SingleStoreBackend()):
            with patch.object(state, "backend", backend):
                cid = flow.start_call(None)
                await flow.on_transcript(cid, "caller", "Desk lamp driver")
                if isinstance(backend, state.SingleStoreBackend):
                    writes = []
                    def execute(sql, *args):
                        writes.append((sql, args))
                        return 1
                    backend.available = True
                    with patch.object(backend, "_exec", side_effect=execute), patch.object(backend, "get_call", side_effect=lambda call_id: backend.calls[call_id]):
                        await flow.verify_caller(cid, None)
                    assert any("state='screening'" in sql for sql, _ in writes)
                    assert any("state='pending'" in sql and args[1] == "expected" for sql, args in writes if sql.startswith("UPDATE calls SET state=?"))
                else:
                    await flow.verify_caller(cid, None)
                assert backend.calls[cid].state == "confirmed"
                assert backend.calls[cid].decided_by == "expected"
    c.portal.call(expected_safety)
    from guard.brain import context
    from scripts import senso_load
    import httpx
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as directory, patch.object(context, "_LOCAL_PATH", Path(directory) / "expected.local.json", create=True):
        local = context._LOCAL_PATH
        assert context.expected("blue vase parcel") is None
        row = {"id": "local-vase", "kind": "delivery", "who": "vase courier",
               "keywords": {"specific": ["blue vase"], "generic": ["parcel"]},
               "note": "Delivery for your vase order."}
        local.write_text(json.dumps([row]))
        assert context.expected("blue vase parcel").source_id == "local-vase"
        with patch.dict(os.environ, {"FAKE": "0", "REAL_SENSO": "1"}), patch.object(context, "_search", return_value=None):
            assert context.expected("blue vase parcel").source_id == "local-vase", "local additions must not need a Senso upload"
        local.write_text(json.dumps([{**row, "until": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()}]))
        assert context.expected("blue vase parcel") is None
        local.write_text(json.dumps([{**row, "id": "order-desk-lamp"}]))
        assert context.expected("desk lamp parcel") is None
        assert context.expected("blue vase parcel").source_id == "order-desk-lamp"
        local.unlink()
        assert context.expected("desk lamp parcel").source_id == "order-desk-lamp"
        today = datetime.now(timezone.utc).date()
        for until, live in ((today.isoformat(), True), ((today - timedelta(days=1)).isoformat(), False),
                            ((datetime.now(timezone.utc) + timedelta(hours=1)).replace(tzinfo=None).isoformat(), True),
                            ("not a date", True)):
            local.write_text(json.dumps([{**row, "until": until}]))
            assert bool(context.expected("blue vase parcel")) == live, until
        local.write_text(json.dumps([{"id": "order-desk-lamp", "expected": False}, row]))
        assert context.expected("desk lamp parcel") is None
        assert context.expected("blue vase parcel").source_id == "local-vase"
        assert context.expected("coffee beans delivery").source_id == "order-coffee"
        class FakeClient:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def post(self, *a, **k):
                class R:
                    def raise_for_status(self): pass
                    def json(self): return {"results": [{"title": "order-desk-lamp", "score": 0.9, "rank": 1}]}
                return R()
        with patch.dict(os.environ, {"SENSO_API_KEY": "x"}), patch.object(context.httpx, "Client", FakeClient):
            rows, disabled, _ = context._load()
            assert "order-desk-lamp" in disabled
            assert context._search("desk lamp parcel", rows, disabled) is None, "a Senso hit on a switched-off id"
        for bad in ("", "{half", '{"id": "x"}', '[1, 2]'):
            local.write_text(bad)
            assert context.expected("desk lamp parcel").source_id == "order-desk-lamp", bad
            assert context.expected("coffee beans delivery").source_id == "order-coffee", bad
        local.unlink()
    ok("expectation overlay: live additions, replacements, expiry and removal")
    assert len(context.records()) == 5
    for words, kind in (("desk lamp", "delivery"), ("coffee beans", "delivery"),
                        ("Oak Clinic office callback", "callback"), ("washing machine repair", "appointment")):
        assert context.expected(words).kind == kind
    assert context.expected("archived receipt book") is None
    assert context.expected("just saying hello") is None
    for words in ("driver delivery parcel", "doctor office appointment", "desk", "lamp", "coffeehouse"):
        assert context.expected(words) is None, words
    with patch.object(context, "_search", side_effect=AssertionError("network forbidden")):
        for switches in ({"FAKE": "1", "REAL_SENSO": "1"}, {"FAKE": "0", "REAL_SENSO": "0"}):
            with patch.dict(os.environ, switches):
                assert context.expected("desk lamp").source == "file"
    def senso_response(request):
        assert request.url.path == "/api/v1/org/search/context"
        assert json.loads(request.content)["query"] in ("desk lamp", "driver parcel delivery", "desk")
        return httpx.Response(200, json={"results": [
            {"title": "order-desk-lamp", "score": 0.9, "rank": 1}]})
    client_type = httpx.Client
    with patch.dict(os.environ, {"FAKE": "0", "REAL_SENSO": "1", "SENSO_API_KEY": "test-key"}):
        with patch.object(context.httpx, "Client", side_effect=lambda **kw: client_type(
                transport=httpx.MockTransport(senso_response), **kw)):
            match = context.expected("desk lamp")
            assert match.source == "senso" and match.source_id == "order-desk-lamp"
            assert context.expected("driver parcel delivery") is None
            assert context.expected("desk") is None
        with patch.object(context, "_search", side_effect=RuntimeError("private detail")), patch.object(context.logging, "getLogger") as logger:
            assert context.expected("desk lamp").source == "file"
            logger.return_value.warning.assert_called_once_with("Senso unavailable; using file expectations")
        with patch.object(context.queue.Queue, "get", side_effect=context.queue.Empty), patch.object(context, "_search", return_value=None):
            assert context.expected("desk lamp").source == "file"
    uploads = []
    polls = []
    def load_response(request):
        if request.method == "POST":
            row = json.loads(request.content)
            uploads.append(row)
            return httpx.Response(202, json={"kb_node_id": row["title"]})
        polls.append(request.url.path)
        assert len(uploads) == 5, "upload all documents before polling"
        return httpx.Response(200, json={"content": {"processing_status": "complete"}})
    with httpx.Client(base_url=context._BASE, transport=httpx.MockTransport(load_response), timeout=2.5) as client:
        senso_load.load(client, context.records())
    assert len(uploads) == len(polls) == 5
    assert {row["title"] for row in uploads} == {row["id"] for row in context.records()}
    preview = subprocess.check_output([sys.executable, "scripts/senso_load.py"], text=True)
    assert preview.strip() == "File expectations: 5 records; no upload"
    ok("expectations: local records, Senso switches, retrieval, error/timeout fallback, loader preview")
    scam = {"claimed_name": "Jakob", "caller_number": "+15550999999", "lines": SCAM}
    scam_id, scam_events = run(c, "scam, tap no", {**scam, "tap": "no", "tap_after_s": 0.5}, "denied",
        BASE + ["owner.notified", "call.ended"])
    ping = next(e["data"]["text"] for e in scam_events if e["type"] == "ping.sent")
    assert "Mum" in ping and "$2,000" in ping and "wire" in ping.lower()
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

    def exercise_code_word_privacy():
        cid = flow.start_call(None)
        c.portal.call(flow.on_transcript, cid, "caller", "This is Jakob, please wire $2000")
        for spoken, masked in [("it is Brunno", "it is ••••"), ("bruno!", "••••!")]:
            c.portal.call(flow.on_transcript, cid, "caller", spoken)
            lines = [e.data["text"] for e in state.events_since(0)
                     if e.call_id == cid and e.type == "transcript"]
            assert lines[-1] == masked, "finals must mask accepted code-word spellings"
        assert "Brunno" not in flow._caller_text[cid]
        flow.on_partial(cid, "caller", "it is Brunno and")
        assert c.get("/live").json()["caller"]["text"] == "it is •••• and"
        flow.on_partial(cid, "caller", "still speaking")
        assert c.get("/live").json()["caller"]["text"] == "still speaking"
        c.portal.call(flow.verify_caller, cid, "Jakob")
        assert state.get_call(cid).state == "pending"
        assert c.get("/live").json()["caller"] is None, "matching must hide an earlier partial"
        for status in ("pending", "unverified"):
            if status == "unverified":
                c.portal.call(flow._resolve, cid, status, "timeout")
            flow.on_partial(cid, "caller", "the word is Bru")
            assert "caller" not in flow._partials.get(cid, {}), "private partials must not be retained"
            flow.on_partial(cid, "agent", "One moment")
            live = c.get("/live").json()
            assert live["caller"] is None, f"{status} caller partials must stay private"
            assert live["agent"]["text"] == "One moment"
        c.portal.call(flow.on_transcript, cid, "caller", "it is Brunno")
        assert c.get("/live").json()["caller"] is None
        assert [e.data["text"] for e in state.events_since(0)
                if e.call_id == cid and e.type == "transcript"][-1] == "it is ••••"

    with patch.object(flow, "_pings", {}), patch.object(contacts, "_attempts", []), patch.object(
            contacts, "_all", return_value=[contacts.get("jakob").model_copy(
                update={"challenge_answer": "Bruno"})]):
        exercise_code_word_privacy()
    ok("code word privacy: tolerant masking, matched caller partials hidden, unmatched and agent visible")

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
        await flow._resolve(cid, "unverified", "timeout")
        assert not await flow.on_tap(cid, "yes")
        assert await flow.on_tap(cid, "no")
        assert state.get_call(cid).state == "denied" and state.get_call(cid).decided_by == "tap"
        assert not await flow.on_tap(cid, "no")

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
    ok("code words: timeout recovery, redaction, two tries, denial, pending winner")

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
    ok("ping cap: five per contact per UTC day, one warning, code word still works")

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
    ok("code word tool: auth, JSON and object arguments, safe results")

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
    ok("code word states: memory and atomic SQL permit pending/unverified only")

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
    ok("local contacts: partial override and extension by id")

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
                (False, True, None, "confirmed", "tap"),
                (False, True, RuntimeError("offline"), "confirmed", "tap"),
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
            assert state.get_call(cid).decided_by == "tap"

        contact = contacts.get("jakob")
        with patch.object(contact, "known_numbers", []):
            for by, number in (("known_number", "+15550660001"), ("other", "+15550660002"), ("tap", "+15550660003"), ("stepup", "+15550660004")):
                contacts.learn(Call(call_id=number, state="confirmed", contact_id="jakob", caller_number=number, started_at=datetime.now(timezone.utc), decided_by=by))
            assert contact.known_numbers == ["+15550660003", "+15550660004"]

    c.portal.call(exercise_trust_gaps)
    preview = json.loads(subprocess.check_output(
        [sys.executable, "scripts/vapi_setup.py", "--dry-run"], text=True,
        env={**os.environ, "OWNER_NAME": "Alex"}, timeout=30,
    ))
    assert "Alex's assistant" in preview["firstMessage"]
    assert "Alex" in preview["model"]["messages"][0]["content"]
    tools = {tool["function"]["name"]: tool for tool in preview["model"]["tools"]}
    assert set(tools) == {"verify_caller", "check_code_word"}
    assert tools["check_code_word"]["function"]["parameters"]["required"] == ["spoken"]
    assert tools["check_code_word"]["server"]["url"].endswith("/vapi/tool/check_code_word")
    ok("trust gaps: notice, late denial, repeat numbers, direct Yes confirmation, safe learning, setup preview")

    async def exercise_seams():
        from guard.contracts import Claim

        claim = classify.claim("Mum, it's your son, I need $2000 in gift cards")
        assert claim.claimed_name is None and claim.relationship == "son"
        assert claim.amount == "$2000" and claim.method == "gift cards" and claim.quote
        assert classify.flags(claim.quote).latency_ms >= 1
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
        with patch.object(persona, "start", side_effect=AssertionError("Persona must stay off the call path")):
            assert await flow.on_tap(cid, "yes")
            assert state.get_call(cid).state == "confirmed"
            assert state.get_call(cid).decided_by == "tap"
            assert not await flow.on_tap(cid, "yes")
            assert not any(e.call_id == cid and e.data.get("kind") == "stepup"
                           for e in state.events_since(0))
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
    ok("seams: claim extraction, learning, repeat limit, name retry, direct Yes, subscribers")
    # Live words: partials stay in memory, are masked, clear on final, and sim plays both sides.
    with patch.object(contacts, "_all", return_value=[contacts.get("jakob").model_copy(
            update={"challenge_answer": "Bruno"})]):
        cid = flow.start_call("+15550123123")
        assert c.get("/live").json() == {"call_id": cid, "caller": None, "agent": None}
        hook = lambda body: c.post("/vapi/webhook", json={"message": {**body, "call": {"id": cid}}})
        hook({"type": "transcript", "transcriptType": "partial", "role": "user", "transcript": "the word is Bru"})
        live = c.get("/live").json()
        assert live["caller"]["text"] == "the word is" and live["agent"] is None and live["caller"]["ts"]
        hook({"type": "transcript", "transcriptType": "partial", "role": "user", "transcript": "the word is bruno and"})
        assert c.get("/live").json()["caller"]["text"] == "the word is •••• and"
        hook({"type": "transcript", "transcriptType": "partial", "role": "assistant", "transcript": "One moment"})
        assert c.get("/live").json()["agent"]["text"] == "One moment"
        hook({"type": "transcript", "transcriptType": "final", "role": "user", "transcript": "The word is Bruno."})
        live = c.get("/live").json()
        assert live["caller"] is None and live["agent"]["text"] == "One moment"
        hook({"type": "transcript", "transcriptType": "partial", "role": "user", "transcript": "The word"})
        assert c.get("/live").json()["caller"] is None, "late partial of a final line must not return"
        with patch.object(flow.time, "time", return_value=flow.time.time() + 5):
            assert c.get("/live").json()["agent"] is None, "partials expire after 4 s of silence"
        evs = [e for e in c.get("/events").json() if e["call_id"] == cid]
        assert [e["data"]["text"] for e in evs if e["type"] == "transcript"] == ["The word is ••••."]
        assert all("Bru" not in json.dumps(e["data"]) for e in evs)
        hook({"type": "status-update", "status": "ended"})
        assert c.get("/live").json()["call_id"] != cid
    sim_id = c.post("/sim/call", json={"lines": ["Hello there friend"], "word_s": 0.02,
        "agent_lines": {"0": "Who is calling?", "1": "One moment."}}).json()["call_id"]
    grew = set()
    t0 = time.time()
    while time.time() - t0 < 5:
        live = c.get("/live").json()
        if live["call_id"] == sim_id and live["caller"]:
            grew.add(live["caller"]["text"])
        roles = [e["data"]["role"] for e in c.get("/events").json()
                 if e["call_id"] == sim_id and e["type"] == "transcript"]
        if roles == ["agent", "caller", "agent"]:
            break
        time.sleep(0.01)
    assert roles == ["agent", "caller", "agent"], roles
    assert len(grew) >= 2 and all("Hello there friend".startswith(t) for t in grew), grew
    ok("live words: masked partials in memory, cleared on final or silence, sim plays both sides")
    # scam-script match: shows up as an event, rides along in the owner warning, never changes a decision
    gift = {"claimed_name": "Jakob", "caller_number": "+15550999111", "tap": "no", "tap_after_s": 0.5,
            "lines": ["Mum, it's me, I need $2000 in gift cards now, don't tell dad"]}
    flow._pings.clear()
    _, gift_events = run(c, "scam script match", gift, "denied", BASE + ["script.match", "owner.notified", "call.ended"])
    gm = next(e["data"] for e in gift_events if e["type"] == "script.match")
    assert gm["script_id"] == "grandparent_giftcards" and not gm["benign"] and gm["method"] == "keyword", gm
    assert any("This matches a known scam: family emergency, gift cards." in e["data"]["text"]
               for e in gift_events if e["type"] == "owner.notified")
    _, drv_events = run(c, "delivery line, no scam match", {"caller_number": "+15550440077",
        "lines": ["Hi, this is the delivery driver, I have a parcel for you, which door should I use?"]},
        "unverified", ["decision", "action", "owner.notified"])
    dm = [e["data"] for e in drv_events if e["type"] == "script.match"]
    assert all(m["benign"] for m in dm), dm
    assert all("known scam" not in e["data"]["text"] for e in drv_events if e["type"] == "owner.notified")
    for label, bad in (("raises", lambda t: 1 / 0), ("hangs", lambda t: time.sleep(3))):
        flow._pings.clear()  # the daily ping cap is not under test here
        with patch.object(scripts, "match", bad):
            t1 = time.time()
            _, bad_events = run(c, "matcher " + label, {**gift, "caller_number": "+1555099922" + str(len(label))}, "denied",
                                BASE + ["owner.notified", "call.ended"])
            assert time.time() - t1 < 2.5, "matcher must not delay the call"
        assert not any(e["type"] == "script.match" for e in bad_events)
        assert all("known scam" not in e["data"]["text"] for e in bad_events if e["type"] == "owner.notified")
    ok("scam script match: event, owner warning, benign case, raising and hanging matcher change nothing")
    # classifier agreement, stand-in only in fake mode, one match at a time
    import asyncio
    from guard.contracts import Flags
    harmless = "Hi Mum, just calling to say hello, how was your week? Tell dad I said hi."
    scam_line = "Mum, it's me, I need $2000 in gift cards now, don't tell dad"
    def matches(cid, text, flags):
        asyncio.run(scripts.on_text(cid, text, flags))
        return [e.data for e in state.backend.events if e.call_id == cid and e.type == "script.match"]
    assert not [m for m in matches("m-h1", harmless, Flags(money=False)) if not m["benign"]]
    assert not [m for m in matches("m-h2", harmless, Flags(money=True, needs_verification=True)) if not m["benign"]]
    assert not matches("m-s0", scam_line, Flags(money=False)) and "m-s0" not in scripts._done
    assert matches("m-s1", scam_line, Flags(money=True))[0]["script_id"] == "grandparent_giftcards"
    with patch.object(settings, "FAKE", False):
        assert scripts.match(scam_line) is None
    seen = []
    def slow(t):
        seen.append(1); time.sleep(0.3)
        return None
    async def two():
        with patch.object(scripts, "match", slow):
            await asyncio.gather(scripts.on_text("m-b", scam_line, Flags(money=True)), scripts.on_text("m-b", scam_line, Flags(money=True)))
    asyncio.run(two())
    assert len(seen) == 1, seen
    ok("scam match: classifier must agree, no stand-in with FAKE off, harmless family line no scam, one at a time")
    # The person being called is never the claimed person: an extracted "Mum" counts as no name.
    from guard.contracts import Claim
    assert flow._caller_name("Mum", "Mum, I'm in trouble.") is None
    assert flow._caller_name("mommy!", "") is None and flow._caller_name(settings.OWNER_NAME, "") is None
    assert flow._caller_name("Mum,", "Mum, it's Jakob. I'm in trouble.") == "Jakob"
    assert flow._caller_name("Lena", "") == "Lena" and flow._caller_name(None, "it's Jakob") is None
    nana = contacts._all()[0].model_copy(update={"id": "nana", "name": "Grete", "nicknames": ["Nana"]})
    with patch.object(contacts, "_contacts", [*contacts._all(), nana]):
        assert flow._caller_name("Nana", "Hi, it's Nana.") == "Nana", "a contact called Nana keeps her name"
    assert flow._caller_name("Nana", "Hi, it's Nana.") is None, "no Nana contact: a form of address"
    stage_scam = {"claimed_name": "Jakob", "tap": "no", "tap_after_s": 0.5, "lines": [
        "Mum, it's Jakob. I'm in trouble.", "I need $2,000 in gift cards right now.", "Don't tell dad."]}
    for label, number, extract in (("keyword claim", "+15550666601", classify.claim),
                                   ("extractor says Mum", "+15550666602",
                                    lambda t: Claim(claimed_name="Mum", method="gift cards", quote=t))):
        flow._pings.clear()
        with patch.object(classify, "claim", extract):
            _, ev = run(c, "stage scam scene, " + label, {**stage_scam, "caller_number": number}, "denied",
                        BASE + ["claim", "call.ended"])
        assert next(e["data"] for e in ev if e["type"] == "claim")["claimed_name"] == "Jakob"
        assert any(e["type"] == "ping.sent" for e in ev)
        assert any(e["type"] == "decision" and e["data"]["by"] == "tap" and e["data"]["state"] == "denied" for e in ev)
    ok("addressee is never the claimed name; stage scam scene asks Jakob and ends on his No")
_timer.cancel()
print("ALL OK")
