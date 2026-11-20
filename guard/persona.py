"""Identity step-up after a Yes tap: a Persona inquiry link for the real person.

Fake mode (default) returns None, which the flow treats as "no step-up" and confirms by tap.
With REAL_PERSONA=1 and FAKE=0 it creates an inquiry from PERSONA_TEMPLATE_ID and returns a
one-time link. Any failure returns None within ~2.5 s and logs one fixed line; the flow then
resolves `stepup_unavailable` as the contract says.
"""
import logging
import os

from guard import settings

API = "https://withpersona.com/api/v1"
_log = logging.getLogger(__name__)
inquiries: dict[str, str] = {}  # inquiry id -> contact id, for a future webhook to map back


def _headers() -> dict:
    return {"Authorization": f"Bearer {os.environ.get('PERSONA_API_KEY', '')}",
            "Persona-Version": "2023-01-05", "Content-Type": "application/json",
            "Accept": "application/json"}


def start(contact_id: str) -> str | None:
    if not settings.REAL_PERSONA:
        return None  # fake: Persona is off
    template = os.environ.get("PERSONA_TEMPLATE_ID", "")
    if not template or not os.environ.get("PERSONA_API_KEY"):
        _log.warning("Persona is on but not configured")
        return None
    try:
        import httpx

        with httpx.Client(timeout=2.5, headers=_headers()) as http:
            r = http.post(f"{API}/inquiries", json={"data": {"attributes": {
                "inquiry-template-id": template, "reference-id": contact_id}}})
            r.raise_for_status()
            inquiry_id = r.json()["data"]["id"]
            inquiries[inquiry_id] = contact_id
            r = http.post(f"{API}/inquiries/{inquiry_id}/generate-one-time-link")
            r.raise_for_status()
            return r.json()["meta"]["one-time-link"]
    except Exception:
        _log.warning("Persona inquiry failed")  # no exception text: it can carry the key
        return None
