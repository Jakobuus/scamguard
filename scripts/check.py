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
from guard import settings  # noqa: E402

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
    run(c, "scam, tap no", {**scam, "tap": "no", "tap_after_s": 0.5}, "denied",
        BASE + ["owner.notified", "call.ended"])
    run(c, "real, tap yes", {"claimed_name": "Jake", "caller_number": "+15550100001",
        "lines": ["Hi, it's Jakob."], "tap": "yes", "tap_after_s": 0.5}, "confirmed", BASE)
    run(c, "scam, no answer", scam, "unverified", BASE + ["owner.notified"])
    run(c, "credential", {"claimed_name": "Broccoli", "lines": ["I'm calling to book a slot."],
        "credential": "demo-credential"}, "agent_verified",
        ["call.started", "transcript", "credential.verified", "decision", "action"])
print("ALL OK")
