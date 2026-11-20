"""Scan a mailbox for orders, shipping notices and appointments, and feed the expected-calls list and memory.

Two sources behind fetch(): the Gmail API (REAL_GMAIL=1, FAKE=0, GMAIL_ACCESS_TOKEN) or a sample mailbox.
Classification is rule-based and runs before anything else sees the text. Mails that ask for money,
fees or codes never become an expectation; they are listed as suspicious instead.
"""
import base64
import hashlib
import json
import logging
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

from guard import settings
from guard.brain import contacts, memory

_log = logging.getLogger(__name__)
_SAMPLE = Path(__file__).resolve().parents[2] / "data" / "gmail_sample.json"
_API = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
_QUERY = ('newer_than:14d -in:chats (order OR shipped OR shipping OR delivery OR courier OR parcel OR '
          'appointment OR "call you back" OR from:({names}))')
_GENERIC = {"delivery": ["parcel", "driver", "delivery", "courier"],
            "callback": ["office", "callback", "returning"],
            "appointment": ["appointment", "visit", "repair"]}
_status = {"source": None, "last_scan": None, "facts": [], "nodes": [], "edges": []}
_lock = threading.Lock()


# ---------- source ----------
def real() -> bool:
    return settings.REAL_GMAIL and not settings.FAKE and bool(os.environ.get("GMAIL_ACCESS_TOKEN"))


def _decode_name(text: str) -> str:
    # Encoded-word names ("=?UTF-8?Q?...?=") come back raw from address parsing; decode, never raise.
    try:
        return str(make_header(decode_header(text))) if text and "=?" in text else text or ""
    except Exception:
        return text or ""


def _b64(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


def _body(payload: dict) -> str:
    """text/plain part first, HTML (tags stripped) as the fallback."""
    stack, html = [payload], ""
    while stack:
        part = stack.pop()
        data = (part.get("body") or {}).get("data")
        if part.get("mimeType") == "text/plain" and data:
            return _b64(data)
        if part.get("mimeType") == "text/html" and data and not html:
            html = re.sub(r"<[^>]+>", " ", _b64(data))
        stack.extend(part.get("parts") or [])
    return html


def _normalize(msg: dict) -> dict:
    headers = {h["name"].lower(): h["value"] for h in (msg.get("payload") or {}).get("headers", [])}
    try:
        date = parsedate_to_datetime(headers["date"]).date().isoformat()
    except Exception:
        date = datetime.now(timezone.utc).date().isoformat()
    name, addr = parseaddr(headers.get("from", ""))
    return {"id": msg["id"], "from": f"{_decode_name(name)} <{addr}>".strip(), "to": headers.get("to", ""),
            "subject": _decode_name(headers.get("subject", "")), "date": date,
            "snippet": msg.get("snippet", ""), "body": _body(msg.get("payload") or {}),
            "list": "list-unsubscribe" in headers or headers.get("precedence", "").lower() in ("bulk", "list")}


def _fetch_api(limit: int) -> list[dict]:
    import httpx
    names = " OR ".join(c.name for c in contacts._all()) or "nobody"
    headers = {"Authorization": f"Bearer {os.environ['GMAIL_ACCESS_TOKEN']}"}
    with httpx.Client(timeout=3.0, headers=headers) as http:
        r = http.get(_API, params={"q": _QUERY.format(names=names), "maxResults": limit})
        r.raise_for_status()
        out = []
        for ref in (r.json().get("messages") or [])[:limit]:
            m = http.get(f"{_API}/{ref['id']}", params={"format": "full"})
            m.raise_for_status()
            out.append(_normalize(m.json()))
        return out


def _fetch_sample(limit: int) -> list[dict]:
    today = datetime.now(timezone.utc).date()
    out = []
    for row in json.loads(_SAMPLE.read_text())[:limit]:
        row = dict(row)
        row["date"] = (today - timedelta(days=int(row.pop("days_ago", 0)))).isoformat()
        out.append(row)
    return out


def fetch(limit: int = 50) -> list[dict]:
    """[{id, from, to, subject, date, snippet, body}]; [] on any error."""
    try:
        return _fetch_api(limit) if real() else _fetch_sample(limit)
    except Exception as exc:
        _log.warning("Mailbox unavailable: %s", type(exc).__name__)
        return []


# ---------- classification ----------
_ASKS_MONEY = re.compile(
    r"\b(pay|payment of|send)\b.{0,40}\b(fee|fine|charge|duty|tax|invoice)\b|\bcustoms fee\b|\bcard number\b|\bcvv\b|"
    r"\b(verification|security|one[- ]time|sms) code\b|\bgift cards?\b|\bwire\b|\bbank details\b|\bpassword\b", re.I)
_NEGATED = re.compile(r"\b(never|do not|don't|won't|will not)\b", re.I)
_NEWSLETTER = re.compile(r"\bunsubscribe\b|newsletter|\bdigest\b", re.I)
_SHIPPING = re.compile(r"\b(order|shipped|shipping|out for delivery|delivery|courier|parcel)\b", re.I)
_APPOINTMENT = re.compile(r"\bappointment\b", re.I)
_WILL_CALL = re.compile(r"\b(will|may) (call|phone|ring)\b|call you (back|before)|phone you|call before delivery", re.I)
_WINDOW = re.compile(r"between (\d{1,2}(?::\d\d)? ?[ap]m) and (\d{1,2}(?::\d\d)? ?[ap]m)", re.I)
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def _asks_money(text: str) -> bool:
    # A sentence that warns against such requests ("we will never ask for your card number") is not a request.
    return any(_ASKS_MONEY.search(s) for s in _sentences(text) if not _NEGATED.search(s))


def _sender(frm: str) -> str:
    name, addr = parseaddr(frm)
    name = _decode_name(name).strip().strip('"')
    domain = addr.split("@", 1)[1].split(".")[0] if "@" in addr else ""
    return name or domain.title()


def _contact_for(name: str):
    return next((c for c in contacts._all()
                 if any(name.casefold() == n.casefold() for n in [c.name, *c.nicknames])), None)


_NOT_ITEM = {"order", "parcel", "package", "delivery", "courier", "will", "call", "before", "is", "out", "for", "has", "been"}


def _item(text: str) -> str:
    for pattern in (r"^item:\s*(.+)$", r"\bwith the ([a-z][a-z \-]{2,30}?) from\b",
                    r"\byour ([a-z][a-z \-]{2,30}?) (?:order|parcel|delivery)\b"):
        for m in re.finditer(pattern, text, re.I | re.M):
            item = m.group(1).strip().lower()
            if item and not set(item.split()) & _NOT_ITEM:
                return item
    return ""


def _until(msg: dict, text: str, default_days: int) -> str:
    base = datetime.fromisoformat(msg["date"]).date()
    low = text.lower()
    if "tomorrow" in low:
        return (base + timedelta(days=1)).isoformat()
    if "today" in low:
        return base.isoformat()
    for i, day in enumerate(_WEEKDAYS):
        if re.search(rf"\b{day}\b", low):
            return (base + timedelta(days=(i - base.weekday()) % 7 or 7)).isoformat()
    return (base + timedelta(days=default_days)).isoformat()


def _eid(kind: str, key: str) -> str:
    return "gmail-" + hashlib.sha1(f"{kind}|{key}".encode()).hexdigest()[:10]


def _record(kind: str, merchant: str, item: str, msg: dict, text: str, will_call: bool) -> dict:
    words = [item] if item else []
    if item and " " in item and len(item.split()[-1]) >= 4:
        words.append(item.split()[-1])  # "wireless headphones" is also said as "headphones"
    if kind != "delivery" or not item:
        words.append(merchant)
    window = _WINDOW.search(text)
    when = f" between {window.group(1)} and {window.group(2)}" if window else ""
    who = {"delivery": f"courier for the {item or merchant} order{when}",
           "callback": f"{merchant} returning your call",
           "appointment": f"{merchant} about your appointment"}[kind]
    note = (f"From your email \"{msg['subject']}\" ({msg['date']})" +
            (": a call is mentioned." if will_call else "."))
    return {"id": _eid(kind, (item or merchant).lower()), "kind": kind, "who": who, "note": note,
            "keywords": {"specific": list(dict.fromkeys(w for w in words if w)),
                         "generic": list(_GENERIC[kind])},
            "until": _until(msg, text, 14 if kind == "appointment" else 7), "source": "gmail"}


def _facts_for(contact, text: str) -> list[str]:
    low, n, out = text.lower(), contact.name, []
    if re.search(r"\bdemo\b", low):
        out.append(f"{n} emailed about demoing the call guard together.")
    if "whatsapp" in low:
        out.append(f"{n} messages on WhatsApp before calling.")
    if re.search(r"\bvisit\b", low):
        out.append(f"{n} emailed about a visit.")
    return out or [f"{n} emailed the owner."]


def scan() -> dict:
    """Classify the mailbox. Never raises; an unreadable source gives an empty result."""
    source = "gmail" if real() else "sample"
    try:
        msgs = fetch()
        expected: dict[str, dict] = {}
        facts, suspicious, ignored, per_contact = [], [], 0, {}
        for msg in sorted(msgs, key=lambda m: m["date"]):  # oldest first: the newest mail names the entry
            text = f"{msg['subject']}\n{msg.get('body') or msg.get('snippet', '')}"
            merchant = _sender(msg["from"])
            contact = _contact_for(merchant)
            if contact:
                for fact in _facts_for(contact, text):
                    mine = per_contact.setdefault(contact.id, [])
                    if len(mine) < 2 and fact not in mine:
                        mine.append(fact)
                        facts.append({"contact_id": contact.id, "text": fact, "message_id": msg["id"],
                                      "subject": msg["subject"], "date": msg["date"]})
                continue
            if _asks_money(text):
                suspicious.append({"message_id": msg["id"], "subject": msg["subject"], "date": msg["date"],
                                   "reason": "asks for a fee, code or card details"})
                continue
            if msg.get("list") or (_NEWSLETTER.search(text) and not _SHIPPING.search(msg["subject"])):
                ignored += 1
                continue
            will_call = bool(_WILL_CALL.search(text))
            kinds = []
            if _SHIPPING.search(msg["subject"]) or _SHIPPING.search(text[:300]):
                kinds.append("delivery")
            if _APPOINTMENT.search(text):
                kinds.append("appointment")
                if will_call:
                    kinds.append("callback")
            item = _item(text) if "delivery" in kinds else ""
            if "delivery" in kinds and not item:
                kinds.remove("delivery")  # no item: nothing a caller could be matched on
            if not kinds:
                ignored += 1
                continue
            for kind in kinds:
                rec = _record(kind, merchant, item if kind == "delivery" else "", msg, text, will_call)
                rec["_msg"] = {"id": msg["id"], "subject": msg["subject"], "date": msg["date"]}
                if rec["id"] in expected:
                    rec["until"] = max(rec["until"], expected[rec["id"]]["until"])  # same order, several mails
                expected[rec["id"]] = rec
        return {"source": source, "scanned": len(msgs), "expected": list(expected.values()), "facts": facts,
                "suspicious": suspicious, "ignored": ignored}
    except Exception as exc:
        _log.warning("Mailbox scan failed: %s", type(exc).__name__)
        return {"source": source, "scanned": 0, "expected": [], "facts": [], "suspicious": [], "ignored": 0}


# ---------- apply ----------
def _is_gmail(row) -> bool:
    return isinstance(row, dict) and str(row.get("id", "")).startswith("gmail-")


def apply(result: dict) -> dict:
    """Write the expectations to the overlay (replacing earlier gmail- rows) and hand facts to memory."""
    from guard import account  # same overlay file and lock as the account app
    rows = [{k: v for k, v in r.items() if k != "_msg"} for r in result.get("expected", [])]
    with account._LOCK:
        local = account._read_list(account.EXPECTED_PATH)
        old = {r["id"]: r for r in local if _is_gmail(r)}
        for row in rows:
            if old.get(row["id"], {}).get("expected") is False:
                row["expected"] = False  # the owner switched this one off; a rescan keeps it off
        if rows or old:
            account.EXPECTED_PATH.parent.mkdir(parents=True, exist_ok=True)
            account.EXPECTED_PATH.write_text(json.dumps([r for r in local if not _is_gmail(r)] + rows, indent=2))
    added = 0
    for fact in result.get("facts", []):
        if fact["text"] not in {i["text"] for i in memory.learned(fact["contact_id"])}:
            memory.remember(fact["text"], fact["contact_id"])  # background write when Cognee is on
            added += 1
    nodes, edges = _graph(result)
    with _lock:
        _status.update(source=result.get("source"), last_scan=datetime.now(timezone.utc).isoformat(),
                       facts=[f["text"] for f in result.get("facts", [])], nodes=nodes, edges=edges)
    return {"expected": len(rows), "facts": len(result.get("facts", [])), "facts_new": added,
            "suspicious": len(result.get("suspicious", [])), "ignored": result.get("ignored", 0)}


def _graph(result: dict) -> tuple[list[dict], list[dict]]:
    nodes, edges, seen = [], [], set()

    def email(m):
        nid = f"email:{m.get('id') or m.get('message_id')}"
        if nid not in seen:
            seen.add(nid)
            nodes.append({"id": nid, "type": "email", "label": m["subject"][:34],
                          "detail": f"Email \"{m['subject']}\", {m['date']}", "origin": "Gmail"})
        return nid
    for r in result.get("expected", []):
        nodes.append({"id": f"exp:{r['id']}", "type": "expected", "label": r["who"][:34],
                      "detail": r["note"], "origin": "Gmail"})
        edges.append({"from": email(r["_msg"]), "to": f"exp:{r['id']}", "label": "mentions"})
        edges.append({"from": f"exp:{r['id']}", "to": "owner", "label": "expected"})
    for i, f in enumerate(result.get("facts", [])):
        nid = f"gfact:{f['contact_id']}:{i}"
        nodes.append({"id": nid, "type": "fact", "label": f["text"][:34], "detail": f["text"], "origin": "Gmail"})
        edges.append({"from": email(f), "to": nid, "label": "mentions"})
        edges.append({"from": nid, "to": f"contact:{f['contact_id']}"})
    return nodes, edges


def fact_texts() -> set[str]:
    """Facts that came from the mailbox, so a page can show their origin."""
    with _lock:
        return set(_status["facts"])


def status() -> dict:
    from guard import account
    with account._LOCK:
        mine = [r for r in account._read_list(account.EXPECTED_PATH) if _is_gmail(r)]
    with _lock:
        s = dict(_status)
    return {"source": s["source"] or ("gmail" if real() else "sample"), "last_scan": s["last_scan"],
            "expected_from_gmail": len(mine), "facts_from_gmail": len(s["facts"]),
            "nodes": s["nodes"], "edges": s["edges"]}
