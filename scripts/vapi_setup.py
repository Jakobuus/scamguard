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

log = logging.getLogger(__name__)


def assistant_payload(base_url: str, secret: str = "") -> dict:
    headers = {"X-Vapi-Secret": secret} if secret else {}
    return {
        "name": "mum-guard",
        "firstMessage": "Hello, this is Mum's assistant. Who is calling, please?",
        "model": {
            "provider": "openai",
            "model": "gpt-4.1",
            "messages": [{"role": "system", "content": (
                "You are the personal assistant of the phone's owner, Mum's assistant. "
                "Greet the caller and ask who is calling. As soon as they identify themselves, "
                "call verify_caller with their claimed_name. Then keep them politely on the line: "
                "'One moment, I'm getting her.' Do not promise she will answer or agree to send "
                "money, make payments, or share private information. Never tell the caller they "
                "are verified based on a tool result. Wait politely for the backend outcome. "
                "If a later system message supplies a challenge question, ask that question now."
            )}],
            "tools": [{
                "type": "function",
                "async": False,
                "function": {
                    "name": "verify_caller",
                    "description": "Ask the real person to verify this caller's claimed identity.",
                    "parameters": {"type": "object", "properties": {
                        "claimed_name": {"type": "string"}}, "required": ["claimed_name"]},
                },
                "server": {"url": base_url + "/vapi/tool/verify_caller",
                           "headers": headers, "timeoutSeconds": 20},
            }],
        },
        "server": {"url": base_url + "/vapi/webhook", "headers": headers},
        "serverMessages": ["status-update", "transcript", "end-of-call-report", "tool-calls"],
        "monitorPlan": {"controlEnabled": True, "controlAuthenticationEnabled": False},
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
