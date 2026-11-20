import json
from pathlib import Path

from guard.contracts import Contact, Lookup

_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "contacts.json"


def _all() -> list[Contact]:
    return [Contact(**c) for c in json.loads(_PATH.read_text())]


def get(contact_id: str) -> Contact | None:
    return next((c for c in _all() if c.id == contact_id), None)


def lookup(claimed_name: str | None, caller_number: str | None) -> Lookup:
    name = (claimed_name or "").strip().lower()
    for c in _all():
        if name and name in [c.name.lower(), *[n.lower() for n in c.nicknames]]:
            known = caller_number in c.known_numbers
            where = "Calling from a known number." if known else "Calling from an unknown number."
            return Lookup(contact=c, number_known=known, note=f"{c.name} ({c.relationship}). {where}")
    return Lookup(contact=None, number_known=False,
                  note=f"Nobody called {claimed_name or 'that'} in the circle.")
