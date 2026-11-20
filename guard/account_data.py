"""Read-only numbers and graph for the account page, derived from the call event stream.

Three functions, no arguments: risk_by_contact(), stats(), graph(). Each is cached for a few
seconds and falls back to an empty result of the same shape; nothing here writes or blocks a call.
"""
import json
import math
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from guard import settings, state
from guard.brain import context, contacts, memory

_DATA = Path(__file__).resolve().parent.parent / "data"
_ACCOUNT = _DATA / "account.local.json"
_TTL = 3.0
_cache: dict[str, tuple[float, object]] = {}
_lock = threading.Lock()

OUTCOMES = [("blocked", "Blocked"), ("confirmed", "Confirmed by tap"), ("unverified", "Unverified"),
            ("expected", "Expected call"), ("known_number", "Known number"), ("other", "Other")]
_OUTCOME_LABEL = dict(OUTCOMES)
TACTICS = [("money", "Money"), ("urgency", "Urgency"), ("secrecy", "Secrecy"),
           ("authority_or_kinship", "Authority or kinship")]
CHANNEL_LABEL = {"whatsapp": "WhatsApp", "telegram": "Telegram", "email": "Email", "call": "Phone call",
                 "code_word": "Code word", "none": "No answer"}


def _cached(key, build, empty):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < _TTL:
            return hit[1]
    try:
        value = build()
    except Exception:  # never let the page break the call path
        value = empty()
    with _lock:
        _cache[key] = (time.time(), value)
    return value


def _account() -> dict:
    try:
        data = json.loads(_ACCOUNT.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _mask(number) -> str:
    digits = re.sub(r"\D", "", str(number or ""))
    return f"•••• {digits[-4:]}" if digits else "••••"


def _parse_amount(text) -> float | None:
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)", str(text or ""))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _iso(ts) -> str:
    return ts.isoformat() if isinstance(ts, datetime) else str(ts)


def _aware(ts) -> datetime:
    if not isinstance(ts, datetime):
        ts = datetime.fromisoformat(str(ts))
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _outcome(decision) -> str:
    if not decision:
        return "other"
    st, by = decision.get("state"), decision.get("by")
    if st == "denied":
        return "blocked"
    if st == "unverified":
        return "unverified"
    if st == "confirmed":
        if by == "expected":
            return "expected"
        if by == "known_number":
            return "known_number"
        return "confirmed"
    return "other"


_events: list = []
_last_id = 0
_events_lock = threading.Lock()


def _all_events() -> list:
    """Full read once, then only new events: the store's lock is shared with the call path."""
    global _last_id
    with _events_lock:
        fresh = state.events_since(_last_id)
        if fresh:
            _events.extend(fresh)
            _last_id = max(e.id for e in fresh)
        return list(_events)


def _calls() -> list[dict]:
    """One summary dict per call, oldest first."""
    by_call: dict[str, dict] = {}
    for ev in _all_events():
        d = ev.data or {}
        c = by_call.setdefault(ev.call_id, {
            "call_id": ev.call_id, "ts": _aware(ev.ts), "caller_number": None, "tactics": set(),
            "claimed_name": None, "amount": None, "method": None, "asks_for": None,
            "contact_id": None, "asked": False, "decision": None, "scam_type": None, "scam_score": -1})
        t = ev.type
        if t == "call.started":
            c["ts"] = _aware(ev.ts)
            c["caller_number"] = d.get("caller_number")
        elif t == "flags":
            c["tactics"] |= {k for k, _ in TACTICS if d.get(k)}
        elif t == "claim":
            for k in ("claimed_name", "amount", "method", "asks_for"):
                if d.get(k):
                    c[k] = d[k]
        elif t == "contact.lookup":
            if d.get("contact"):
                c["contact_id"] = d["contact"].get("id")
        elif t == "ping.sent":
            c["asked"] = True
        elif t == "decision":
            c["decision"] = d
        elif t == "script.match":
            if not d.get("benign") and d.get("title") and (d.get("score") or 0) > c["scam_score"]:
                c["scam_type"], c["scam_score"] = d["title"], d.get("score") or 0
    out = sorted(by_call.values(), key=lambda c: c["ts"])
    for c in out:
        c["outcome"] = _outcome(c["decision"])
    return out


def _empty_risk() -> dict:
    return {}


def risk_by_contact() -> dict[str, dict]:
    return _cached("risk", _build_risk, _empty_risk)


def _build_risk() -> dict:
    risk: dict[str, dict] = {}
    for c in _calls():
        cid = c["contact_id"]
        if not cid:
            continue
        r = risk.setdefault(cid, {"claimed": 0, "blocked": 0, "confirmed": 0, "last_ts": None})
        r["claimed"] += 1
        r["blocked"] += c["outcome"] in ("blocked", "unverified")
        r["confirmed"] += c["outcome"] == "confirmed"
        r["last_ts"] = _iso(c["ts"])  # oldest first, so the last write is the newest
    return risk


def _empty_stats() -> dict:
    return {
        "headline": {"calls_screened": 0, "questions_asked": 0, "blocked": 0, "money_not_sent": 0,
                     "median_seconds_to_decide": None},
        "outcomes": [{"key": k, "label": lb, "count": 0} for k, lb in OUTCOMES],
        "over_time": _buckets([]),
        "channels_answered": [],
        "tactics": [{"key": k, "label": lb, "count": 0} for k, lb in TACTICS],
        "recent": [],
    }


def _buckets(calls: list[dict]) -> list[dict]:
    now = datetime.now(timezone.utc)
    span = (now - calls[0]["ts"]) if calls else timedelta(0)
    if span <= timedelta(hours=1):
        size, n, fmt = timedelta(minutes=5), 12, "%H:%M"
    elif span <= timedelta(hours=24):
        size, n, fmt = timedelta(hours=2), 12, "%H:%M"
    else:
        size = timedelta(days=1)
        n = min(14, max(8, math.ceil(span / size)))
        fmt = "%a" if n <= 7 else "%a %d"
    out = []
    for i in range(n):
        end = now - size * (n - 1 - i)
        start = end - size
        inside = [c for c in calls if start < c["ts"] <= end]
        out.append({"label": end.astimezone().strftime(fmt), "calls": len(inside),
                    "blocked": sum(c["outcome"] in ("blocked", "unverified") for c in inside)})
    return out


def stats() -> dict:
    return _cached("stats", _build_stats, _empty_stats)


def _build_stats() -> dict:
    calls = _calls()
    if not calls:
        return _empty_stats()
    counts = {k: 0 for k, _ in OUTCOMES}
    tactics = {k: 0 for k, _ in TACTICS}
    answered: dict[str, int] = {}
    money = 0.0
    paid_seen = set()
    for c in calls:
        if c["decision"]:  # undecided calls stay out of the outcome counts
            counts[c["outcome"]] += 1
        for k in c["tactics"]:
            tactics[k] += 1
        if c["outcome"] in ("blocked", "unverified"):
            amount = _parse_amount(c["amount"])
            if amount and (c["caller_number"], amount) not in paid_seen:  # once per caller and amount
                paid_seen.add((c["caller_number"], amount))
                money += amount
        d = c["decision"] or {}
        by = d.get("by")
        kind = (d.get("channel") or "telegram") if by == "tap" else (
            "code_word" if by == "code_word" else "none" if by == "timeout" else None)
        if kind:
            answered[kind] = answered.get(kind, 0) + 1
    asked = [c for c in calls if c["asked"] and c["decision"] and c["decision"].get("seconds_to_decide") is not None]
    pool = asked or [c for c in calls if c["decision"] and c["decision"].get("seconds_to_decide") is not None]
    secs = sorted(c["decision"]["seconds_to_decide"] for c in pool)
    median = None
    if secs:
        mid = len(secs) // 2
        median = round(secs[mid] if len(secs) % 2 else (secs[mid - 1] + secs[mid]) / 2, 1)
    recent = []
    for c in reversed(calls[-12:]):
        d = c["decision"] or {}
        recent.append({
            "call_id": c["call_id"], "ts": _iso(c["ts"]), "caller_number": c["caller_number"],
            "claimed_name": c["claimed_name"], "contact_id": c["contact_id"], "amount": c["amount"],
            "method": c["method"], "asks_for": c["asks_for"],
            "tactics": [k for k, _ in TACTICS if k in c["tactics"]],
            "outcome": c["outcome"],
            "outcome_label": _OUTCOME_LABEL[c["outcome"]] if c["decision"] else "In progress",
            "decided_by": d.get("by"), "channel": d.get("channel"),
            "seconds_to_decide": d.get("seconds_to_decide"), "scam_type": c["scam_type"]})
    return {
        "headline": {"calls_screened": len(calls), "questions_asked": sum(c["asked"] for c in calls),
                     "blocked": counts["blocked"] + counts["unverified"],
                     "money_not_sent": round(money, 2), "median_seconds_to_decide": median},
        "outcomes": [{"key": k, "label": lb, "count": counts[k]} for k, lb in OUTCOMES],
        "over_time": _buckets(calls),
        "channels_answered": [{"kind": k, "label": CHANNEL_LABEL.get(k, k.title()), "count": n}
                              for k, n in sorted(answered.items(), key=lambda kv: -kv[1]) if n],
        "tactics": [{"key": k, "label": lb, "count": tactics[k]} for k, lb in TACTICS],
        "recent": recent,
    }


# ---- graph ----

def _empty_graph() -> dict:
    return {"source": "local", "nodes": [{"id": "owner", "type": "owner", "label": settings.OWNER_NAME,
                                          "detail": "The person the guard protects."}], "edges": []}


def _short(text: str, n: int = 26) -> str:
    text = " ".join(str(text).split())
    if len(text) <= n:
        return text
    return text[:n].rsplit(" ", 1)[0].rstrip(",;:.") + "…"


def _facts(people: list[dict]) -> tuple[str, list[tuple[str | None, str]]]:
    """(source, [(contact id or None, fact)]) without a slow network call."""
    by_name = {p.name.casefold(): p.id for p in people}
    out: list[tuple[str | None, str]] = []
    source = "local"
    try:
        if memory.real() and memory._notes:  # already-fetched lines only; no request is made here
            source = "cognee"
            for cid, line in list(memory._notes.items()):
                if line:
                    out.append((cid, line))
    except Exception:
        out, source = [], "local"
    if not out:
        source = "local"
        try:
            seed = json.loads(memory.SEED.read_text())
        except Exception:
            seed = {}
        for p in seed.get("people", []):
            for f in p.get("facts", []):
                out.append((by_name.get(str(p.get("name", "")).casefold()), memory.scrub(f)))
        for f in seed.get("household", []):
            out.append((None, memory.scrub(f)))
    return source, out


def _origin(text: str) -> str:
    # HACK: keyword rules on the sample text decide where a fact would have come from.
    t = text.casefold()
    if re.search(r"\b(really did call|said it was not them|confirmed it was|pretended to be|on \d{4}-)", t):
        return "Learned from a verified call"
    if re.search(r"\b(is .{0,30}(son|daughter|mother|father|brother|sister|friend)|lives|usually|never|agreed|always|orders|studies)\b", t):
        return "Entered by you"
    return "Sample data"


def graph() -> dict:
    return _cached("graph", _build_graph, _empty_graph)


def _build_graph() -> dict:
    acct = _account()
    owner = (acct.get("profile") or {}).get("owner_name") or settings.OWNER_NAME
    nodes = [{"id": "owner", "type": "owner", "label": owner,
              "detail": f"{owner} is the person the guard answers calls for."}]
    edges = []
    removed = set(acct.get("removed") or [])
    people = [c for c in contacts._all() if c.id not in removed]
    for c in people:
        nid = f"contact:{c.id}"
        nodes.append({"id": nid, "type": "contact", "label": c.name,
                      "detail": f"{c.name} is {owner}'s {c.relationship}. The guard asks them before putting a caller through as them."})
        edges.append({"from": nid, "to": "owner", "label": c.relationship})
        for i, num in enumerate(c.known_numbers):
            nodes.append({"id": f"num:{c.id}:{i}", "type": "number", "label": _mask(num),
                          "detail": f"A number the guard knows belongs to {c.name}."})
            edges.append({"from": f"num:{c.id}:{i}", "to": nid})
        chans = ((acct.get("contacts") or {}).get(c.id) or {}).get("channels")
        if chans is None:  # same defaults the contacts API shows
            chans = ([{"kind": "email", "enabled": True}] if c.email else []) + (
                [{"kind": "call", "enabled": True}] if c.known_numbers else [])
        for pos, ch in enumerate(x for x in chans if x.get("enabled")):
            kind = ch.get("kind", "")
            nodes.append({"id": f"ch:{c.id}:{kind}", "type": "channel", "kind": kind,
                          "label": CHANNEL_LABEL.get(kind, str(kind).title()),
                          "detail": f"The guard asks {c.name} on {CHANNEL_LABEL.get(kind, kind)} (choice {pos + 1})."})
            edges.append({"from": f"ch:{c.id}:{kind}", "to": nid})
    for n in (acct.get("profile") or {}).get("numbers") or []:
        if n.get("protected"):
            nid = f"own:{n.get('id') or _mask(n.get('number'))}"
            nodes.append({"id": nid, "type": "number", "label": f"{_short(n.get('label') or 'My number', 14)} {_mask(n.get('number'))}",
                          "detail": f"One of {owner}'s own numbers. Calls to it are screened."})
            edges.append({"from": nid, "to": "owner", "label": "protected"})
    try:
        rows = context.records()
    except Exception:
        rows = []
    for r in rows:
        if r.get("expected", True) is False:
            continue
        words = r["id"].split("-")
        label = " ".join(words[1:] + [r["kind"]]) if words[0] == "order" else " ".join(words)
        nodes.append({"id": f"exp:{r['id']}", "type": "expected", "label": label.capitalize(),
                      "detail": f"{r['note']} The guard lets this call through without a question."})
        edges.append({"from": f"exp:{r['id']}", "to": "owner", "label": "expected"})
    source, facts = _facts(people)
    ids = {c.id for c in people}
    for i, (cid, text) in enumerate(facts[:8]):
        nodes.append({"id": f"fact:{i}", "type": "fact", "label": _short(text), "detail": _short(text, 200),
                      "origin": _origin(text)})
        edges.append({"from": f"fact:{i}", "to": f"contact:{cid}" if cid in ids else "owner"})
    merged: dict[tuple, dict] = {}
    for c in _calls():  # oldest first, so the last write per key is the newest
        if not (c["claimed_name"] or _parse_amount(c["amount"])):
            continue
        key = (c["contact_id"], c["caller_number"], c["asks_for"] or c["amount"] or "")
        m = merged.pop(key, None)
        merged[key] = {**c, "n": (m["n"] if m else 0) + 1}
    for c in reversed(list(merged.values())[-10:]):
        blocked = c["outcome"] in ("blocked", "unverified")
        real = c["outcome"] == "confirmed"
        if real:
            label = f"Real call from {c['claimed_name'] or 'a contact'}"
            detail = f"{c['claimed_name'] or 'The contact'} confirmed it was them, so the call went through."
        else:
            label = c["asks_for"] or c["amount"] or c["scam_type"] or f"Call as {c['claimed_name']}"
            who = f"claiming to be {c['claimed_name']}" if c["claimed_name"] else "pretending to be a service"
            detail = f"Someone {who}" + (f" asked for {c['asks_for']}." if c["asks_for"] else " called.")
            if c["scam_type"]:
                detail += f" Looks like a known scam: {c['scam_type']}."
            detail += " The guard stopped it." if blocked else f" Outcome: {_OUTCOME_LABEL[c['outcome']].lower()}."
        if c["n"] > 1:
            detail += f" Tried {c['n']} times."
        nid = f"attempt:{c['call_id']}"
        nodes.append({"id": nid, "type": "attempt", "label": _short(label), "detail": detail, "blocked": blocked and not real})
        edges.append({"from": nid, "to": f"contact:{c['contact_id']}" if c["contact_id"] in ids else "owner"})
    return {"source": source, "nodes": nodes, "edges": edges,
            "memory_note": ("Live memory." if source == "cognee" else
                            "Sample memory. With the memory layer connected, only what a verified answer proved is remembered.")}
