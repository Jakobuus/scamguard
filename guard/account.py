"""Account app: sign-in, identity check, profile, trusted contacts. Reads call data only."""
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse

from guard import channels, settings
from guard.brain import contacts as brain_contacts
from guard.contracts import Contact

log = logging.getLogger("guard.account")
router = APIRouter()
_DATA = Path(__file__).resolve().parent.parent / "data"
ACCOUNT_PATH = _DATA / "account.local.json"
CONTACTS_PATH = _DATA / "contacts.local.json"
EXPECTED_PATH = _DATA / "expected.local.json"
_EXPECTED_BUILTIN = _DATA / "expected.json"
_LOCK = threading.Lock()
COOKIE = "guard_session"
KINDS = ("whatsapp", "telegram", "email", "call")
DEFAULT_QUESTION = "What is the family code word?"


# ---------- storage ----------
def _load() -> dict:
    data = {}
    if ACCOUNT_PATH.exists():
        try:
            data = json.loads(ACCOUNT_PATH.read_text())
        except ValueError:
            data = {}
    data.setdefault("users", {})
    data.setdefault("contacts", {})
    data.setdefault("removed", [])
    return data


def _save(data: dict) -> None:
    ACCOUNT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ACCOUNT_PATH.write_text(json.dumps(data, indent=2))


def _load_local_contacts() -> list[dict]:
    if CONTACTS_PATH.exists():
        try:
            return json.loads(CONTACTS_PATH.read_text())
        except ValueError:
            return []
    return []


def _save_local_contacts(items: list[dict]) -> None:
    CONTACTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONTACTS_PATH.write_text(json.dumps(items, indent=2))
    brain_contacts._contacts = None  # HACK: drop the guard's cache so the next call sees the change


# ---------- session ----------
def _secret() -> bytes:
    s = os.environ.get("APP_SECRET")
    if not s:
        if not settings.FAKE:
            raise HTTPException(503, "Sign-in is not configured on this server.")
        s = "dev-only-secret"  # HACK: fixed dev secret, fake mode only
    return s.encode()


def _sign(payload: str) -> str:
    return hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()


def _make_token(uid: str) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"u": uid, "e": int(time.time()) + 7 * 86400}).encode()).decode()
    return body + "." + _sign(body)


def _read_token(token: str | None) -> str | None:
    if not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    try:
        if not hmac.compare_digest(sig, _sign(body)):
            return None
        d = json.loads(base64.urlsafe_b64decode(body.encode()))
    except (ValueError, HTTPException):
        return None
    return d["u"] if d.get("e", 0) > time.time() else None


def _https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"


def _set_cookie(request: Request, response: Response, uid: str) -> None:
    response.set_cookie(COOKIE, _make_token(uid), httponly=True, samesite="lax", path="/",
                        secure=_https(request), max_age=7 * 86400)


def _me_out(u: dict) -> dict:
    return {"id": u["id"], "name": u.get("name", ""), "email": u.get("email", ""), "picture": u.get("picture"),
            "verified": bool(u.get("verified")), "verified_at": u.get("verified_at"),
            "verified_via": u.get("verified_via")}


def _user(request: Request) -> dict:
    uid = _read_token(request.cookies.get(COOKIE))
    with _LOCK:
        data = _load()
        u = data["users"].get(uid) if uid else None
    if not u or data.get("owner_id") not in (None, uid):
        raise HTTPException(401, "sign in")
    return u


def _claim(uid: str) -> None:
    """One guard, one owner: the first Google account to sign in owns it; the demo user never locks it."""
    with _LOCK:
        data = _load()
        owner = data.get("owner_id")
        if owner not in (None, "demo", uid):
            raise HTTPException(403, "This guard is already set up by another account.")
        data["owner_id"] = uid
        _save(data)


def _demo_login() -> bool:
    return not os.environ.get("GOOGLE_CLIENT_ID") or os.environ.get("DEMO_LOGIN") == "1"


def _guard_number() -> str:
    return os.environ.get("GUARD_NUMBER") or "+1 (555) 010-0199"


def _persona_template() -> str:
    """Non-empty when the hosted Persona check is configured (a ready link, a template version, or a template)."""
    return settings.persona_template()


# ---------- pages and auth ----------
@router.get("/app")
def app_page():
    page = Path(__file__).resolve().parent.parent / "web" / "app.html"
    if page.exists():
        return FileResponse(page, headers={"Cache-Control": "no-store"})
    return HTMLResponse("<!doctype html><title>KinCheck</title><p>KinCheck is loading.</p>")


@router.get("/api/config")
def config():
    live = channels.enabled()
    return {"google_client_id": os.environ.get("GOOGLE_CLIENT_ID") or None, "demo_login": _demo_login(),
            "persona_mode": "hosted" if _persona_template() else "demo", "owner_name": settings.OWNER_NAME,
            "guard_number": _guard_number(), "fake": settings.FAKE,
            "channels_live": {"whatsapp": "whatsapp" in live, "telegram": "telegram" in live,
                              "email": "email" in live, "call": True}}


def _upsert_user(uid: str, **fields) -> dict:
    with _LOCK:
        data = _load()
        u = data["users"].setdefault(uid, {"id": uid})
        u.update({k: v for k, v in fields.items() if v is not None or k == "picture"})
        _save(data)
        return dict(u)


@router.post("/api/auth/google")
def auth_google(body: dict, request: Request, response: Response):
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    token = (body or {}).get("credential")
    if not client_id or not token:
        raise HTTPException(401, "Google sign-in failed.")
    try:
        r = httpx.get("https://oauth2.googleapis.com/tokeninfo", params={"id_token": token}, timeout=3)
        info = r.json() if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        info = {}
    if not info.get("sub") or info.get("aud") != client_id:
        raise HTTPException(401, "Google sign-in failed.")
    _claim("g_" + info["sub"])
    u = _upsert_user("g_" + info["sub"], name=info.get("name") or info.get("email", ""),
                     email=info.get("email", ""), picture=info.get("picture"))
    _set_cookie(request, response, u["id"])
    return _me_out(u)


@router.post("/api/auth/demo")
def auth_demo(request: Request, response: Response):
    if not _demo_login():
        raise HTTPException(403, "Demo sign-in is switched off.")
    _claim("demo")
    with _LOCK:
        owner_name = _profile_out(_load())["owner_name"]
    u = _upsert_user("demo", name=owner_name, email="", picture=None)
    _set_cookie(request, response, "demo")
    return _me_out(u)


@router.post("/api/auth/logout")
def auth_logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/api/me")
def me(request: Request):
    return _me_out(_user(request))


# ---------- identity check ----------
@router.post("/api/verify/start")
def verify_start(request: Request):
    user = _user(request)
    template = _persona_template()
    if not template:
        return {"mode": "demo", "url": None}
    base = (os.environ.get("PUBLIC_BASE_URL") or str(request.base_url)).rstrip("/")
    extra = {"reference-id": user["id"], "redirect-uri": base + "/app"}
    ready = os.environ.get("PERSONA_INQUIRY_URL", "").strip()
    if ready:  # a prepared link; add who is checking and where to come back to when it does not say so
        add = {k: v for k, v in extra.items() if k + "=" not in ready}
        return {"mode": "hosted", "url": ready + (("&" if "?" in ready else "?") + urlencode(add) if add else "")}
    version = os.environ.get("PERSONA_TEMPLATE_VERSION_ID", "").strip()
    # A published template version works in an environment the template is not deployed to yet.
    params = {"inquiry-template-version-id": version} if version else {"inquiry-template-id": os.environ.get("PERSONA_TEMPLATE_ID", "").strip()}
    params.update(extra)
    if os.environ.get("PERSONA_ENVIRONMENT_ID"):
        params["environment-id"] = os.environ["PERSONA_ENVIRONMENT_ID"]
    return {"mode": "hosted", "url": "https://inquiry.withpersona.com/verify?" + urlencode(params)}


def _persona_check(user_id: str, inquiry_id: str | None) -> bool | None:
    """True = Persona says finished and passed, False = it says not, None = could not ask."""
    key = os.environ.get("PERSONA_API_KEY")
    if not key:
        return None
    headers = {"Authorization": "Bearer " + key, "Persona-Version": "2023-01-05"}
    base = "https://api.withpersona.com/api/v1/inquiries"
    try:
        with httpx.Client(timeout=3, headers=headers) as http:
            if inquiry_id:
                r = http.get(f"{base}/{inquiry_id}")
            else:  # newest first by default
                r = http.get(base, params={"filter[reference-id]": user_id, "page[size]": 1})
        if r.status_code != 200:
            raise ValueError("status")
        data = r.json()["data"]
    except Exception:
        log.warning("Persona lookup unavailable; trusting the redirect")  # never log the exception: it can carry the key
        return None
    rec = data[0] if isinstance(data, list) and data else None if isinstance(data, list) else data
    if not rec:
        return False
    attrs = rec.get("attributes") or {}
    return attrs.get("reference-id") == user_id and attrs.get("status") in ("completed", "approved")


@router.post("/api/verify/complete")
def verify_complete(request: Request, body: dict | None = None):
    user = _user(request)
    hosted = bool(_persona_template())
    checked = False
    if hosted:
        result = _persona_check(user["id"], (body or {}).get("inquiry_id"))
        if result is False:
            raise HTTPException(409, "Persona has not finished the check yet.")
        checked = result is True
    # HACK: without a Persona API key, or when Persona cannot be reached (the account may lack API
    # access), we trust the redirect; no webhook
    u = _upsert_user(user["id"], verified=True, verified_via="persona" if hosted else "demo",
                     verified_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                     verified_inquiry=(body or {}).get("inquiry_id"), verified_checked=checked)
    return _me_out(u)


# ---------- profile ----------
def _profile_out(data: dict) -> dict:
    p = data.get("profile") or {}
    return {"owner_name": p.get("owner_name") or settings.OWNER_NAME, "guard_number": _guard_number(),
            "numbers": p.get("numbers") or [{"id": "n_home", "label": "Home", "number": "+1 (555) 010-0142",
                                             "protected": True}]}


@router.get("/api/profile")
def get_profile(request: Request):
    _user(request)
    with _LOCK:
        return _profile_out(_load())


@router.put("/api/profile")
def put_profile(body: dict, request: Request):
    _user(request)
    numbers = [{"id": n.get("id") or "n_" + uuid.uuid4().hex[:8], "label": str(n.get("label", "")),
                "number": str(n.get("number", "")), "protected": bool(n.get("protected", True))}
               for n in body.get("numbers") or []]
    with _LOCK:
        data = _load()
        data["profile"] = {"owner_name": str(body.get("owner_name") or settings.OWNER_NAME).strip(),
                           "numbers": numbers}
        if "demo" in data["users"]:
            data["users"]["demo"]["name"] = _profile_out(data)["owner_name"]
        _save(data)
        return _profile_out(data)


# ---------- contacts ----------
def _builtin_ids() -> set[str]:
    return {c["id"] for c in json.loads(brain_contacts._PATH.read_text())}


def _default_channels(c: Contact) -> list[dict]:
    out = []
    if c.email:
        out.append({"kind": "email", "handle": c.email, "enabled": True})
    if c.known_numbers:
        out.append({"kind": "call", "handle": c.known_numbers[0], "enabled": True})
    return out


def _risk(risks: dict, cid: str) -> dict:
    return risks.get(cid) or {"claimed": 0, "blocked": 0, "confirmed": 0, "last_ts": None}


def _risks() -> dict:
    try:
        from guard import account_data
        return account_data.risk_by_contact()
    except ImportError:
        return {}
    except Exception:
        return {}  # HACK: stats must never break the contact list


def _contact_out(c: Contact, data: dict, risks: dict, builtin: set) -> dict:
    stored = (data["contacts"].get(c.id) or {}).get("channels")
    return {"id": c.id, "name": c.name, "nicknames": list(c.nicknames), "relationship": c.relationship,
            "known_numbers": list(c.known_numbers), "has_code_word": bool(c.challenge_answer),
            "channels": stored if stored is not None else _default_channels(c),
            "risk": _risk(risks, c.id), "builtin": c.id in builtin}


def _visible(data: dict) -> list[Contact]:
    gone = set(data["removed"])
    return [c for c in brain_contacts._all() if c.id not in gone]  # HACK: the guard still knows removed built-ins


def _clean_channels(raw) -> list[dict]:
    out = []
    for ch in raw or []:
        if ch.get("kind") in KINDS:
            out.append({"kind": ch["kind"], "handle": str(ch.get("handle", "")).strip(),
                        "enabled": bool(ch.get("enabled", True))})
    return out


def _slug(name: str, taken: set) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-") or "contact"
    cid, n = base, 2
    while cid in taken:
        cid, n = f"{base}-{n}", n + 1
    return cid


@router.get("/api/contacts")
def list_contacts(request: Request):
    _user(request)
    risks = _risks()
    with _LOCK:
        data = _load()
        builtin = _builtin_ids()
        return [_contact_out(c, data, risks, builtin) for c in _visible(data)]


def _write_contact(cid: str | None, body: dict, creating: bool):
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(422, "Give the contact a name.")
    code = str(body.get("code_word") or "").strip()
    if creating and not code:
        raise HTTPException(422, "Set a code word for this contact.")
    with _LOCK:
        data = _load()
        existing_all = {c.id: c for c in brain_contacts._all()}
        if creating:
            cid = _slug(name, set(existing_all) | set(data["removed"]))
            old = None
        else:
            old = existing_all.get(cid)
            if old is None or cid in data["removed"]:
                raise HTTPException(404, "No such contact.")
        entry = {
            "id": cid, "name": name,
            "nicknames": [str(n).strip() for n in body.get("nicknames") or [] if str(n).strip()],
            "relationship": str(body.get("relationship") or "").strip(),
            "known_numbers": [str(n).strip() for n in body.get("known_numbers") or [] if str(n).strip()],
            "telegram_chat_id": old.telegram_chat_id if old else None,
            "email": old.email if old else None,
            "challenge_question": old.challenge_question if old else DEFAULT_QUESTION,
            "challenge_answer": code or (old.challenge_answer if old else ""),
        }
        local = [e for e in _load_local_contacts() if e.get("id") != cid]
        local.append(entry)
        _save_local_contacts(local)
        data["contacts"][cid] = {"channels": _clean_channels(body.get("channels"))}
        _save(data)
        c = brain_contacts.get(cid)
        return _contact_out(c, data, _risks(), _builtin_ids())


@router.post("/api/contacts", status_code=201)
def add_contact(body: dict, request: Request):
    _user(request)
    return _write_contact(None, body, True)


@router.put("/api/contacts/{cid}")
def edit_contact(cid: str, body: dict, request: Request):
    _user(request)
    return _write_contact(cid, body, False)


@router.delete("/api/contacts/{cid}")
def delete_contact(cid: str, request: Request):
    _user(request)
    with _LOCK:
        data = _load()
        if cid in _builtin_ids():
            if cid not in data["removed"]:
                data["removed"].append(cid)
        elif brain_contacts.get(cid) is None:
            raise HTTPException(404, "No such contact.")
        _save_local_contacts([e for e in _load_local_contacts() if e.get("id") != cid])
        data["contacts"].pop(cid, None)
        _save(data)
    return {"ok": True}


# ---------- expected calls ----------
_EXPECTED_KINDS = ("delivery", "callback", "appointment")
_GENERIC = {"delivery": ["parcel", "driver", "delivery", "courier"],
            "callback": ["office", "callback", "returning"],
            "appointment": ["appointment", "visit", "repair"]}


def _read_list(path: Path) -> list[dict]:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except ValueError:
            return []
    return []


def _expected_out(rec: dict, builtin: set) -> dict:
    kind = rec.get("kind", "delivery")
    return {"id": rec["id"], "kind": kind, "who": rec.get("who", ""),
            "note": rec.get("note") or f"Expecting a {kind}: {rec.get('who', '')}.",
            "keywords": list((rec.get("keywords") or {}).get("specific") or []),
            "expected": rec.get("expected", True), "builtin": rec["id"] in builtin}


def _expected_merged() -> tuple[list[dict], set]:
    base = _read_list(_EXPECTED_BUILTIN)
    builtin = {r["id"] for r in base}
    merged = {r["id"]: r for r in base}
    for r in _read_list(EXPECTED_PATH):
        merged[r["id"]] = {**merged.get(r["id"], {}), **r}
    return list(merged.values()), builtin


def _clean_words(raw) -> list[str]:
    return [str(w).strip() for w in raw or [] if str(w).strip()]


@router.get("/api/expected")
def list_expected(request: Request):
    _user(request)
    with _LOCK:
        recs, builtin = _expected_merged()
        return [_expected_out(r, builtin) for r in recs]


@router.post("/api/expected", status_code=201)
def add_expected(body: dict, request: Request):
    _user(request)
    who = str(body.get("who") or "").strip()
    words = _clean_words(body.get("keywords"))
    kind = body.get("kind") if body.get("kind") in _EXPECTED_KINDS else "delivery"
    if not who:
        raise HTTPException(422, "Say who is going to call.")
    if not words:
        raise HTTPException(422, "Add at least one word the caller will say.")
    with _LOCK:
        recs, builtin = _expected_merged()
        rec = {"id": _slug(who, {r["id"] for r in recs}), "kind": kind, "who": who,
               "keywords": {"specific": words, "generic": list(_GENERIC[kind])},
               "note": str(body.get("note") or "").strip() or f"Expecting a {kind}: {who}.", "expected": True}
        local = _read_list(EXPECTED_PATH)
        local.append(rec)
        EXPECTED_PATH.parent.mkdir(parents=True, exist_ok=True)
        EXPECTED_PATH.write_text(json.dumps(local, indent=2))
        return _expected_out(rec, builtin)


@router.put("/api/expected/{eid}")
def edit_expected(eid: str, body: dict, request: Request):
    _user(request)
    with _LOCK:
        recs, builtin = _expected_merged()
        rec = next((r for r in recs if r["id"] == eid), None)
        if rec is None:
            raise HTTPException(404, "No such expected call.")
        rec = json.loads(json.dumps(rec))  # full copy for the overlay
        kind_changed = body.get("kind") in _EXPECTED_KINDS and body["kind"] != rec.get("kind")
        if body.get("kind") in _EXPECTED_KINDS:
            rec["kind"] = body["kind"]
        if str(body.get("who") or "").strip():
            rec["who"] = str(body["who"]).strip()
        if body.get("note") is not None:
            rec["note"] = str(body["note"]).strip() or f"Expecting a {rec['kind']}: {rec['who']}."
        kw = rec.setdefault("keywords", {})
        if body.get("keywords") is not None:
            words = _clean_words(body["keywords"])
            if not words:
                raise HTTPException(422, "Add at least one word the caller will say.")
            kw["specific"] = words
        if kind_changed or not kw.get("generic"):
            kw["generic"] = list(_GENERIC[rec["kind"]])
        if body.get("expected") is not None:
            rec["expected"] = bool(body["expected"])
        local = [r for r in _read_list(EXPECTED_PATH) if r.get("id") != eid] + [rec]
        EXPECTED_PATH.parent.mkdir(parents=True, exist_ok=True)
        EXPECTED_PATH.write_text(json.dumps(local, indent=2))
        return _expected_out(rec, builtin)


@router.delete("/api/expected/{eid}")
def delete_expected(eid: str, request: Request):
    _user(request)
    with _LOCK:
        recs, builtin = _expected_merged()
        if eid in builtin:
            raise HTTPException(409, "Built-in entries can only be switched off.")
        local = _read_list(EXPECTED_PATH)
        if not any(r.get("id") == eid for r in local):
            raise HTTPException(404, "No such expected call.")
        EXPECTED_PATH.write_text(json.dumps([r for r in local if r.get("id") != eid], indent=2))
    return {"ok": True}


# ---------- stats and graph ----------
_OUTCOMES = [("blocked", "Blocked"), ("confirmed", "Confirmed"), ("unverified", "Unverified"),
             ("expected", "Expected"), ("known_number", "Known number"), ("other", "Other")]
_TACTICS = [("money", "Money"), ("urgency", "Urgency"), ("secrecy", "Secrecy"),
            ("authority_or_kinship", "Authority or kinship")]


@router.get("/api/stats")
def stats(request: Request):
    _user(request)
    try:
        from guard import account_data
        return account_data.stats()
    except ImportError:
        pass
    return {"headline": {"calls_screened": 0, "questions_asked": 0, "blocked": 0, "money_not_sent": 0,
                         "median_seconds_to_decide": None},
            "outcomes": [{"key": k, "label": lbl, "count": 0} for k, lbl in _OUTCOMES],
            "over_time": [{"label": "", "calls": 0, "blocked": 0} for _ in range(8)],
            "channels_answered": [], "tactics": [{"key": k, "label": lbl, "count": 0} for k, lbl in _TACTICS],
            "recent": []}


@router.get("/api/graph")
def graph(request: Request):
    _user(request)
    try:
        from guard import account_data
        return account_data.graph()
    except ImportError:
        pass
    return {"source": "local", "nodes": [{"id": "owner", "type": "owner", "label": settings.OWNER_NAME,
                                          "detail": "The person the guard protects."}], "edges": []}
