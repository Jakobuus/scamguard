"""Configure the inbound assistant; --dry-run prints a safe preview without network calls."""
import argparse
import json
import logging
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from guard import settings  # noqa: E402
from guard.brain import contacts  # noqa: E402

log = logging.getLogger(__name__)


def boost_terms() -> list[str]:
    """Words the transcriber should favour: names and relationships from the data, plus call vocabulary.
    Never includes code words."""
    terms = [settings.OWNER_NAME]
    for c in contacts._all():
        terms += [c.name, *c.nicknames, c.relationship]
    terms += ["teammate", "son", "daughter", "hackathon", "delivery", "parcel", "code word"]
    seen, out = set(), []
    for t in terms:
        t = (t or "").strip()
        if t and t.casefold() not in seen:
            seen.add(t.casefold())
            out.append(t)
    return out


def assistant_payload(base_url: str, secret: str = "") -> dict:
    headers = {"X-Vapi-Secret": secret} if secret else {}
    return {
        "name": f"{settings.OWNER_NAME.lower()}-guard",
        "firstMessage": f"Hello, this is {settings.OWNER_NAME}'s assistant. Who is calling and what is it about?",
        # Deepgram nova-3 takes boosted vocabulary as `keyterm` (`keywords` is for older models).
        "transcriber": {"provider": "deepgram", "model": "nova-3", "language": "en", "smartFormat": True,
                        "keyterm": boost_terms(), "endpointing": 300},
        "backgroundSpeechDenoisingPlan": {"smartDenoisingPlan": {"enabled": True}},
        "startSpeakingPlan": {"waitSeconds": 0.8},
        "model": {
            "provider": "openai",
            "model": "gpt-4.1",
            "messages": [{"role": "system", "content": (
                f"You are {settings.OWNER_NAME}'s assistant. Ask who is calling and what it is about. "
                "Never offer or confirm a name, or reveal who is in the owner's circle. "
                "Never agree to pay, buy or send anything, or take payment details. "
                "As soon as the caller says who they are in any form — a name, a relationship to the owner, "
                "or an organisation or role such as a delivery driver, courier, doctor's office, pharmacy, "
                "shop about an order, tradesperson, or callback about an appointment — call verify_caller. "
                "Pass the personal name they gave in claimed_name (empty string if no personal name), "
                "and their own words about who they are and why they call in reason. "
                "If they have not said who they are or why they call after your greeting, ask once more, briefly, "
                "and then call verify_caller with their reply. "
                "If you did not clearly understand who is calling, ask them once to repeat their name slowly before you call verify_caller. "
                "Call verify_caller only once per call. After it, say one short sentence that you are trying to reach "
                "the owner and that the caller may say the code word if they cannot use their phone, then stay "
                "quiet until the caller speaks or a system message arrives. Never repeat that you are checking or holding. "
                "Follow system messages, such as asking for the first name or a challenge question. "
                "If the caller says any word that could be a code word (a single name-like word such as an answer), "
                "or says they cannot use their phone, or a system message asks for it, call check_code_word "
                "immediately with exactly what they said; you may do this at any time, even while waiting.  Never guess, repeat or hint at the word. Follow the tool result. "
                "Never reveal an expected answer. "
                "For sales or surveys, say the owner is not interested and end politely. "
                "For other callers with a harmless reason, take a short message."
            )}],
            "tools": [{
                "type": "function",
                "async": False,
                "function": {
                    "name": "verify_caller",
                    "description": "Check a caller by their stated name, relationship, organisation or role and reason for calling, including deliveries and appointments.",
                    "parameters": {"type": "object", "properties": {
                        "claimed_name": {"type": "string"},
                        "reason": {"type": "string", "description": "Caller's own words about their organisation, role or relationship and why they call."}},
                        "required": ["claimed_name"]},
                },
                "server": {"url": base_url + "/vapi/tool/verify_caller",
                           "headers": headers, "timeoutSeconds": 20},
            }, {
                "type": "function",
                "async": False,
                "function": {
                    "name": "check_code_word",
                    "description": "Check the caller's spoken family code word without revealing it.",
                    "parameters": {"type": "object", "properties": {
                        "spoken": {"type": "string"}}, "required": ["spoken"]},
                },
                "server": {"url": base_url + "/vapi/tool/check_code_word",
                           "headers": headers, "timeoutSeconds": 20},
            }],
        },
        "server": {"url": base_url + "/vapi/webhook", "headers": headers},
        "serverMessages": ["status-update", "transcript", "end-of-call-report", "tool-calls"],
        "monitorPlan": {"controlEnabled": True, "controlAuthenticationEnabled": False, "listenEnabled": True},
        "maxDurationSeconds": 120,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    base_url = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    if args.dry_run:
        # Never expose the actual webhook secret, including in the preview.
        preview = assistant_payload(base_url or "https://YOUR_HOST",
                                    "<redacted>" if os.environ.get("VAPI_SERVER_SECRET") else "")
        print(json.dumps(preview, indent=2))
        return 0
    if not os.environ.get("VAPI_SERVER_SECRET"):
        log.warning("Vapi setup needs VAPI_SERVER_SECRET; use --dry-run for a preview")
        return 1
    if not settings.REAL_VAPI or settings.FAKE:
        log.warning("Vapi setup disabled; set REAL_VAPI=1 and FAKE=0 or use --dry-run")
        return 1
    key = os.environ.get("VAPI_API_KEY", "")
    phone_id = os.environ.get("VAPI_PHONE_NUMBER_ID", "")
    if not key or not phone_id or not base_url:
        log.warning("Vapi setup needs VAPI_API_KEY, VAPI_PHONE_NUMBER_ID and PUBLIC_BASE_URL")
        return 1
    payload = assistant_payload(base_url, os.environ.get("VAPI_SERVER_SECRET", ""))
    try:
        with httpx.Client(base_url="https://api.vapi.ai", timeout=20,
                          headers={"Authorization": f"Bearer {key}", "User-Agent": "curl/8"}) as client:
            phone = client.get(f"/phone-number/{phone_id}")
            phone.raise_for_status()
            # HACK: reuse the phone's assigned assistant instead of keeping local setup state.
            assistant_id = os.environ.get("VAPI_ASSISTANT_ID") or phone.json().get("assistantId")
            if assistant_id:
                response = client.patch(f"/assistant/{assistant_id}", json=payload)
            else:
                response = client.post("/assistant", json=payload)
            response.raise_for_status()
            assistant_id = response.json()["id"]
            response = client.patch(f"/phone-number/{phone_id}", json={"assistantId": assistant_id})
            response.raise_for_status()
        print(assistant_id)
        return 0
    except Exception:
        # Responses and exceptions can contain credentials; keep the failure message generic.
        log.warning("Vapi setup failed; existing local fake behavior remains available")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
