import json

from fastapi import APIRouter, Request

from guard import state

router = APIRouter()


def say(call_id: str, text: str) -> None:
    state.emit(call_id, "action", {"kind": "say", "text": text})  # fake: no phone


def hangup(call_id: str) -> None:
    state.emit(call_id, "action", {"kind": "hangup", "text": "Call ended by the guard."})


def forward(call_id: str) -> None:
    state.emit(call_id, "action", {"kind": "forward", "text": "Call forwarded to the owner."})


def add_message(call_id: str, text: str) -> None:
    state.emit(call_id, "action", {"kind": "challenge", "text": text})


@router.post("/vapi/webhook")
async def webhook():
    # TODO lane 1: handle status-update, transcript, end-of-call-report
    return {}


@router.post("/vapi/tool/verify_caller")
async def verify_caller(req: Request):
    from guard import flow  # late import: flow imports this module

    try:
        body = await req.json()
    except Exception:
        body = {}
    msg = body.get("message", body) if isinstance(body, dict) else {}
    calls = msg.get("toolCallList") or msg.get("toolCalls") or [{}]
    tc = calls[0] if calls else {}
    args = (tc.get("function") or {}).get("arguments") or tc.get("arguments") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            args = {}
    vapi_call = msg.get("call") or {}
    call_id = vapi_call.get("id") or "vapi-unknown"
    if state.get_call(call_id) is None:
        flow.start_call((vapi_call.get("customer") or {}).get("number"), call_id)
    await flow.verify_caller(call_id, args.get("claimed_name"))
    # TODO lane 1: confirm the real Vapi payload shape against a live call
    return {"results": [{"toolCallId": tc.get("id"), "result": "ping sent, keep the caller holding"}]}
