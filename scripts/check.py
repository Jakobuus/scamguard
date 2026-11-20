"""Plays the scenes through /sim/call in-process. Exit non-zero on any failure."""
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
from guard import settings, flow, persona, state
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
    run(c, "scam, no answer", scam, "unverified", BASE + ["owner.notified"])
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
    # Repeated denied/unverified claims cannot keep pinging the real contact.
    repeat_id, repeat_events = run(c, "third attempt", scam, "denied", ["decision", "owner.notified"])
    assert c.get(f"/calls/{repeat_id}").json()["decided_by"] == "repeat_attempt"
    assert not any(e["type"] == "ping.sent" for e in repeat_events)
    assert any("Third attempt" in e["data"].get("text", "") for e in repeat_events)
    assert contacts.attempts_today("jakob") == 3
    assert "+15550999999" not in contacts.get("jakob").known_numbers
    assert "+15550100002" in contacts.get("jakob").known_numbers

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
    c.portal.call(exercise_seams)
    print("OK seams: claim extraction, learning, repeat limit, name retry, step-up, subscribers")
print("ALL OK")
