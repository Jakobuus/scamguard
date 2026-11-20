import hmac
import json
import logging
import os
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request

from guard import settings, state
from guard.contracts import Call

router = APIRouter()
log = logging.getLogger(__name__)
# HACK: live control URLs and webhook deduplication last only for this process.
_control_urls: dict[str, str] = {}
_ended: set[str] = set()
_TOOL_RESULT = "Ping sent. Keep the caller holding and tell them you are checking."


def _control(call_id: str, payload: dict) -> bool:
    if not settings.REAL_VAPI or settings.FAKE:
        return False
    url = _control_urls.get(call_id)
    if not url:
        log.warning("Vapi %s: no control URL; using fake action", payload["type"])
        return False
    try:
        httpx.post(url, json=payload, timeout=5).raise_for_status()
        return True
    except Exception:
        # Never log exceptions: they may contain the private control URL.
        log.warning("Vapi %s failed; using fake action", payload["type"])
        return False


def say(call_id: str, text: str, end_after: bool = False) -> None:
    state.emit(call_id, "action", {"kind": "say", "text": text})
    _control(call_id, {"type": "say", "content": text, "endCallAfterSpoken": end_after})


def hangup(call_id: str) -> None:
    state.emit(call_id, "action", {"kind": "hangup", "text": "Call ended by the guard."})
    _control(call_id, {"type": "end-call"})


def forward(call_id: str) -> None:
    state.emit(call_id, "action", {"kind": "forward", "text": "Call forwarded to the owner."})
    if not settings.REAL_VAPI or settings.FAKE:
        return
    if not _control_urls.get(call_id):
        log.warning("Vapi transfer: no control URL; using fake action")
        return
    number = os.environ.get("TRANSFER_TO_NUMBER", "").strip()
    if number:
        if _control(call_id, {"type": "transfer", "destination": {"type": "number", "number": number},
                              "content": "Transferring your call now."}):
            return
    else:
        log.warning("Vapi transfer: no destination; using callback message")
    say(call_id, "You are verified. She will call you right back.", end_after=True)


def add_message(call_id: str, text: str) -> None:
    state.emit(call_id, "action", {"kind": "challenge", "text": text})
    _control(call_id, {"type": "add-message", "message": {"role": "system", "content":
        f"Ask the caller this challenge question now: {text}"}, "triggerResponseEnabled": True})


def _authorize(req: Request) -> None:
    secret = os.environ.get("VAPI_SERVER_SECRET")
    if settings.REAL_VAPI and not settings.FAKE and not secret:
        raise HTTPException(status_code=503, detail="Vapi server secret is not configured")
    if secret and not hmac.compare_digest(
        req.headers.get("X-Vapi-Secret", "").encode(), secret.encode()
    ):
        raise HTTPException(status_code=401, detail="Invalid Vapi secret")


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


async def _message(req: Request) -> dict:
    try:
        body = _dict(await req.json())
        return _dict(body.get("message", body))
    except Exception:
        return {}


def _remember_control(call_id: str, call: dict) -> None:
    url = _dict(call.get("monitor")).get("controlUrl")
    if url is None:
        return
    try:
        parsed = urlsplit(url) if isinstance(url, str) else None
        valid = (parsed is not None and parsed.scheme == "https"
                 and parsed.hostname is not None and parsed.hostname.endswith(".vapi.ai")
                 and not parsed.username and not parsed.password)
    except ValueError:
        valid = False
    if not valid:
        log.warning("Vapi control URL rejected")
        return
    if call_id not in _control_urls:
        _control_urls[call_id] = url
        if "control_url" in Call.model_fields and state.get_call(call_id) is not None:
            state.update_call(call_id, control_url=url)


@router.post("/vapi/webhook")
async def webhook(req: Request):
    from guard import flow  # late import: flow imports this module

    _authorize(req)
    msg = await _message(req)
    call = _dict(msg.get("call"))
    call_id = call.get("id")
    if not isinstance(call_id, str) or not call_id:
        return {}
    try:
        kind = msg.get("type")
        if kind == "status-update" and msg.get("status") == "in-progress":
            if state.get_call(call_id) is None:
                flow.start_call(_dict(call.get("customer")).get("number"), call_id=call_id)
            _remember_control(call_id, call)
        elif kind == "transcript" and msg.get("transcriptType") == "final":
            text = msg.get("transcript")
            if isinstance(text, str) and state.get_call(call_id) is not None:
                await flow.on_transcript(call_id, "caller" if msg.get("role") == "user" else "agent", text)
        elif kind == "end-of-call-report" or (kind == "status-update" and msg.get("status") == "ended"):
            if call_id not in _ended:
                # The decision flow can already have emitted the end event after a denial.
                if not any(e.call_id == call_id and e.type == "call.ended" for e in state.events_since(0)):
                    state.emit(call_id, "call.ended", {})
                _ended.add(call_id)
                _control_urls.pop(call_id, None)
    except Exception:
        log.warning("Vapi webhook failed; acknowledging event")
    return {}


async def _verify(call_id: str, claimed_name) -> None:
    from guard import flow

    try:
        await flow.verify_caller(call_id, claimed_name)
    except Exception:
        log.warning("Vapi caller verification failed; keeping caller on hold")


@router.post("/vapi/tool/verify_caller")
async def verify_caller(req: Request):
    from guard import flow  # late import: flow imports this module

    _authorize(req)
    msg = await _message(req)
    calls = msg.get("toolCallList") or msg.get("toolCalls") or [{}]
    if not isinstance(calls, list):
        calls = [{}]
    call = _dict(msg.get("call"))
    call_id = call.get("id") or "vapi-unknown"
    if not isinstance(call_id, str):
        call_id = "vapi-unknown"
    if state.get_call(call_id) is None:
        flow.start_call(_dict(call.get("customer")).get("number"), call_id=call_id)
    _remember_control(call_id, call)
    results = []
    for item in calls:
        tc = _dict(item)
        args = _dict(tc.get("function")).get("arguments") or tc.get("arguments") or msg
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        # Return before verification can touch Telegram or any other network service.
        flow.spawn(_verify(call_id, _dict(args).get("claimed_name")))
        results.append({"toolCallId": tc.get("id"), "result": _TOOL_RESULT})
    return {"results": results}
