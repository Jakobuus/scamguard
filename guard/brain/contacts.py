import json
from datetime import datetime, timezone
from pathlib import Path

from guard.brain import memory
from guard.contracts import Call, Claim, Contact, Lookup

_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "contacts.json"
_contacts: list[Contact] | None = None
_attempts: list[tuple[datetime, Call]] = []


def _all() -> list[Contact]:
    global _contacts
    if _contacts is None:
        entries = {c["id"]: c for c in json.loads(_PATH.read_text())}
        local = _PATH.with_name("contacts.local.json")
        if local.exists():
            try:
                overlay = json.loads(local.read_text())
            except (OSError, ValueError):
                overlay = []  # HACK: a half-written overlay must never break a call
            for entry in overlay if isinstance(overlay, list) else []:
                try:
                    merged = {**entries.get(entry["id"], {}), **entry}
                    Contact(**merged)
                    entries[entry["id"]] = merged
                except Exception:
                    continue
        _contacts = [Contact(**c) for c in entries.values()]
        memory.warm(_contacts)  # Cognee prefetch in the background; no-op in fake mode
    return _contacts


def get(contact_id: str) -> Contact | None:
    return next((c for c in _all() if c.id == contact_id), None)


def close(a: str, b: str) -> bool:
    """Equal, or one edit apart for words of 5+ letters (speech-to-text spelling drift)."""
    a, b = a.casefold().strip(), b.casefold().strip()
    if a == b:
        return True
    if min(len(a), len(b)) < 5 or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) <= 1
    a, b = sorted((a, b), key=len)
    return any(a == b[:i] + b[i + 1:] for i in range(len(b)))


def candidates(claim: Claim, caller_number: str | None) -> list[Lookup]:
    name = (claim.claimed_name or "").strip().casefold()
    relationship = (claim.relationship or "").strip().casefold()
    found = []
    for contact in _all():
        matches = (any(close(name, n) for n in [contact.name, *contact.nicknames])
                   if name else bool(relationship) and relationship == contact.relationship.casefold())
        if matches:
            known = caller_number in contact.known_numbers
            where = "Calling from a known number." if known else "Calling from an unknown number."
            remembered = memory.note(contact.id)  # enriches the note only; never changes the match
            note = f"{contact.name} ({contact.relationship}). {where}"
            found.append(Lookup(contact=contact, number_known=known,
                                note=f"{note} Memory: {remembered}" if remembered else note))
    return found


def lookup(claimed_name: str | None, caller_number: str | None) -> Lookup:
    found = candidates(Claim(claimed_name=claimed_name), caller_number)
    return found[0] if found else Lookup(note=f"Nobody called {claimed_name or 'that'} in the circle.")


def attempts_today(contact_id: str, caller_number: str | None = None) -> int:
    today = datetime.now(timezone.utc).date()
    return sum(when.date() == today and call.contact_id == contact_id
               and (caller_number is None or call.caller_number == caller_number)
               for when, call in _attempts)


def denied_today(contact_id: str, caller_number: str | None) -> bool:
    today = datetime.now(timezone.utc).date()
    return caller_number is not None and any(
        when.date() == today and call.contact_id == contact_id
        and call.caller_number == caller_number and call.state == "denied"
        and call.decided_by == "tap" for when, call in _attempts)


def learn(call: Call) -> None:
    contact = get(call.contact_id) if call.contact_id else None
    if call.state == "confirmed" and call.decided_by in ("tap", "stepup") and contact and call.caller_number:
        if call.caller_number not in contact.known_numbers:
            contact.known_numbers.append(call.caller_number)
    elif call.state in ("denied", "unverified"):
        _attempts.append((datetime.now(timezone.utc), call.model_copy(deep=True)))
    _remember(call, contact)


def _remember(call: Call, contact: Contact | None) -> None:
    """Tell Cognee only what a verified outcome proves. No numbers, no transcript, no code words."""
    if not contact or not call.decided_by:
        return
    day = (call.decided_at or datetime.now(timezone.utc)).date().isoformat()
    asked = " and asked for money" if call.flags and call.flags.money else ""
    if call.state == "confirmed" and call.decided_by in ("tap", "stepup"):
        memory.remember(f"On {day} {contact.name} really did call from a new number{asked}; "
                        f"{contact.name} confirmed it was them.")
    elif call.state == "denied" and call.decided_by in ("tap", "stepup", "repeat_attempt"):
        memory.remember(f"On {day} someone pretended to be {contact.name}{asked}. "
                        f"{contact.name} said it was not them.")


def reset_demo() -> None:
    """Forget attempts and reload configured numbers, preserving call history."""
    global _contacts
    today = datetime.now(timezone.utc).date()
    _attempts[:] = [(when, call) for when, call in _attempts if when.date() != today]
    _contacts = None
