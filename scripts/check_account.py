"""Quick fake-mode check of the account endpoints. Writes only to temporary files."""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("FAKE", "1")
os.environ.setdefault("DEMO_LOGIN", "1")
os.environ.pop("PERSONA_TEMPLATE_ID", None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from guard import account  # noqa: E402
from guard.app import app  # noqa: E402
from guard.brain import contacts  # noqa: E402

WORD = "zebrafish-pudding"
tmp = Path(tempfile.mkdtemp())
account.ACCOUNT_PATH = tmp / "account.local.json"
account.CONTACTS_PATH = tmp / "contacts.local.json"
import shutil  # noqa: E402

# The guard derives its local overlay path from the built-in file, so point both at the temp folder.
ORIG_PATH = contacts._PATH
shutil.copy(ORIG_PATH, tmp / "contacts.json")
contacts._PATH = tmp / "contacts.json"
account.EXPECTED_PATH = tmp / "expected.local.json"
contacts._contacts = None
seen = []


def call(c, method, path, **kw):
    r = getattr(c, method)(path, **kw)
    seen.append(r.text)
    return r


def ok(cond, msg):
    if not cond:
        print("FAIL:", msg)
        sys.exit(1)
    print("ok:", msg)


try:
    c = TestClient(app)
    cfg = call(c, "get", "/api/config")
    ok(cfg.status_code == 200 and cfg.json()["demo_login"] and cfg.json()["persona_mode"] == "demo", "config")
    ok(call(c, "get", "/api/me").status_code == 401, "me without session is 401")
    ok(call(c, "get", "/app").status_code == 200, "app page")
    me = call(c, "post", "/api/auth/demo").json()
    ok(me["id"] == "demo" and not me["verified"], "demo sign-in")
    ok(me["name"] == cfg.json()["owner_name"] and me["email"] == "", "demo uses owner identity")
    ok(call(c, "get", "/api/me").json()["name"] == me["name"], "me")
    ok(call(c, "post", "/api/verify/start").json()["mode"] == "demo", "verify start")
    done = call(c, "post", "/api/verify/complete", json={}).json()
    ok(done["verified"] and done["verified_via"] == "demo", "verify complete")
    prof = call(c, "get", "/api/profile").json()
    ok(len(prof["numbers"]) == 1, "profile default")
    put = call(c, "put", "/api/profile", json={"owner_name": "Mum", "numbers": [
        {"label": "Home", "number": "+1 (555) 010-0142", "protected": True},
        {"label": "Mobile", "number": "+1 (555) 010-0150", "protected": False}]}).json()
    ok(put["owner_name"] == "Mum" and all(n["id"] for n in put["numbers"]), "profile put")
    ok(call(c, "get", "/api/profile").json() == put, "profile round trip")
    ok(call(c, "get", "/api/me").json()["name"] == put["owner_name"], "demo name follows owner name")
    me = call(c, "post", "/api/auth/demo").json()
    ok(me["name"] == put["owner_name"] and me["email"] == "", "demo sign-in keeps owner identity")

    body = {"name": "Test Cousin", "relationship": "cousin", "known_numbers": ["+15550100077"],
            "channels": [{"kind": "telegram", "handle": "@cousin", "enabled": True},
                         {"kind": "email", "handle": "cousin@example.com", "enabled": False}], "code_word": WORD}
    ok(call(c, "post", "/api/contacts", json={**body, "code_word": ""}).status_code == 422, "code word required")
    r = call(c, "post", "/api/contacts", json=body)
    ok(r.status_code == 201, "add contact")
    cid = r.json()["id"]
    lst = call(c, "get", "/api/contacts").json()
    mine = next((x for x in lst if x["id"] == cid), None)
    ok(mine and mine["has_code_word"] and mine["channels"][0]["kind"] == "telegram", "listed with code word flag")
    ok(contacts.get(cid) is not None and contacts.get(cid).challenge_answer == WORD, "guard knows the contact")
    r = call(c, "put", f"/api/contacts/{cid}", json={**body, "relationship": "second cousin", "code_word": ""})
    ok(r.status_code == 200 and contacts.get(cid).challenge_answer == WORD
       and contacts.get(cid).relationship == "second cousin", "edit keeps old code word")
    ok(call(c, "delete", f"/api/contacts/{cid}").json() == {"ok": True} and contacts.get(cid) is None, "delete")
    first = lst[0]["id"] if lst[0]["id"] != cid else lst[1]["id"]
    call(c, "delete", f"/api/contacts/{first}")
    ok(all(x["id"] != first for x in call(c, "get", "/api/contacts").json()), "built-in hidden after delete")
    st = call(c, "get", "/api/stats")
    ok(st.status_code == 200 and {"headline", "outcomes", "over_time", "channels_answered", "tactics", "recent"} <= set(st.json()), "stats")
    g = call(c, "get", "/api/graph")
    ok(g.status_code == 200 and {"source", "nodes", "edges"} <= set(g.json()), "graph")
    ex = call(c, "get", "/api/expected").json()
    ok(len(ex) > 0 and all(e["builtin"] for e in ex), "expected list")
    r = call(c, "post", "/api/expected", json={"kind": "delivery", "who": "courier for the tea order", "keywords": ["tea"]})
    ok(r.status_code == 201 and r.json()["expected"] and not r.json()["builtin"], "expected add")
    ok(call(c, "post", "/api/expected", json={"kind": "delivery", "who": "x", "keywords": []}).status_code == 422, "expected needs keyword")
    eid = r.json()["id"]
    ok(call(c, "put", f"/api/expected/{eid}", json={"expected": False}).json()["expected"] is False, "expected switch off")
    b = ex[0]["id"]
    ok(call(c, "put", f"/api/expected/{b}", json={"expected": False}).json()["builtin"], "built-in switch off")
    ok(call(c, "delete", f"/api/expected/{b}").status_code == 409, "built-in not deletable")
    ok(call(c, "delete", f"/api/expected/{eid}").json() == {"ok": True}, "expected delete")
    ok(call(c, "post", "/api/auth/logout").json() == {"ok": True}, "logout")
    ok(not any(WORD in s for s in seen), "code word never in a response")
finally:
    contacts._PATH = ORIG_PATH
    contacts._contacts = None
print("all good")
