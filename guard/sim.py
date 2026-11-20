import asyncio
import hmac
import os
import time
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from guard import flow, settings


def _sim_key(secret: str) -> str:
    return hmac.new(secret.encode(), b"sim-key", "sha256").hexdigest()[:20]


def key_ok(req: Request) -> bool:
    if settings.FAKE:
        return True
    secret = os.environ.get("APP_SECRET", "")
    key = req.headers.get("X-Sim-Key", "")
    return bool(secret) and hmac.compare_digest(key.encode(), _sim_key(secret).encode())


def _authorize(req: Request) -> None:
    if not key_ok(req):
        raise HTTPException(403, "Invalid sim key")


router = APIRouter(dependencies=[Depends(_authorize)])
public = APIRouter()  # no key: only says whether a feature exists
_sim_calls: set[str] = set()  # /sim/tap may only decide calls that /sim/call started, never a real one


class SimCall(BaseModel):
    claimed_name: str | None = None
    caller_number: str | None = None
    lines: list[str] = []
    tap: Literal["yes", "no"] | None = None
    tap_after_s: float = 1.0
    credential: str | None = None  # extra: plays the credential path
    # Assistant lines keyed by position: key i is spoken before caller line i; key len(lines)
    # is spoken after the last caller line, while the guard checks.
    agent_lines: dict[int, str] = {}
    word_s: float | None = None  # seconds per word for the live partials; default SIM_WORD_S or 0.07


class SimTap(BaseModel):
    call_id: str
    answer: Literal["yes", "no"]


async def _say(call_id: str, role: str, text: str, word_s: float) -> None:
    """Plays one line like a live call: growing partials word by word, then the final."""
    if word_s > 0:
        words = text.split()
        for i in range(1, len(words) + 1):
            flow.on_partial(call_id, role, " ".join(words[:i]))
            await asyncio.sleep(word_s)
    await flow.on_transcript(call_id, role, text)


async def _play(call_id: str, b: SimCall) -> None:
    word_s = b.word_s if b.word_s is not None else float(os.environ.get("SIM_WORD_S", "0.07"))
    pause = 0.5 if word_s > 0 else 0.3
    for i, line in enumerate(b.lines):
        if b.agent_lines.get(i):
            await _say(call_id, "agent", b.agent_lines[i], word_s)
            await asyncio.sleep(pause)
        await _say(call_id, "caller", line, word_s)
        await asyncio.sleep(pause)
    if b.credential:
        if await flow.on_credential(call_id, b.credential):
            return
    await flow.verify_caller(call_id, b.claimed_name)
    t0 = asyncio.get_running_loop().time()
    closing = b.agent_lines.get(len(b.lines))
    if closing:
        await _say(call_id, "agent", closing, word_s)
    if b.tap:
        await asyncio.sleep(max(0.0, b.tap_after_s - (asyncio.get_running_loop().time() - t0)))
        await flow.on_tap(call_id, b.tap)


@router.post("/sim/reset")
async def sim_reset():
    flow.reset_demo()
    return {"ok": True}


@router.post("/sim/call")
async def sim_call(b: SimCall):
    call_id = flow.start_call(b.caller_number)
    _sim_calls.add(call_id)
    flow.spawn(_play(call_id, b))
    return {"call_id": call_id}


@router.post("/sim/tap")
async def sim_tap(b: SimTap):
    if b.call_id not in _sim_calls:
        raise HTTPException(404, "not a simulated call")
    return {"ok": await flow.on_tap(b.call_id, b.answer)}


# The scam caller: a consented demo impersonation that phones the guard number through Vapi.
SCAM_MAX_S = 80  # the scam-caller assistant hangs up after 75 s
_scam: dict = {}  # id, control_url, t of the one running scam call


def _vapi_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url="https://api.vapi.ai", timeout=15, headers={
        "Authorization": f"Bearer {os.environ.get('VAPI_API_KEY', '')}", "User-Agent": "curl/8.7.1"})


async def _scam_running(client: httpx.AsyncClient) -> bool:
    if not _scam or time.time() - _scam["t"] > SCAM_MAX_S:
        return False
    try:
        r = await client.get(f"/call/{_scam['id']}")
        return r.json().get("status") != "ended"
    except Exception:
        return True


@public.get("/sim/scam-call")
def scam_call_available():
    """The dashboard shows its cloned-voice button only when this is not 404."""
    if settings.FAKE or not settings.REAL_VAPI or not os.environ.get("VAPI_SCAM_PHONE_ID"):
        raise HTTPException(404, "not available")
    return {"available": True, "running": bool(_scam) and time.time() - _scam["t"] < SCAM_MAX_S}


@router.post("/sim/scam-call")
async def scam_call():
    if settings.FAKE or not settings.REAL_VAPI:
        return {"started": False, "reason": "fake mode"}
    if not os.environ.get("VAPI_SCAM_PHONE_ID"):
        return {"started": False, "reason": "no caller number"}
    missing = [n for n in ("VAPI_API_KEY", "VAPI_PHONE_NUMBER_ID") if not os.environ.get(n)]
    if missing:
        return {"started": False, "reason": "missing " + ", ".join(missing)}
    try:
        async with _vapi_client() as client:
            if await _scam_running(client):
                return {"started": False, "reason": "a scam call is already running", "vapi_call_id": _scam["id"]}
            aid = os.environ.get("VAPI_SCAM_ASSISTANT_ID")
            if not aid:
                r = await client.get("/assistant", params={"limit": 100})
                aid = next((a["id"] for a in r.json() if a.get("name") == "scam-caller"), None)
            if not aid:
                return {"started": False, "reason": "no scam-caller assistant; run scripts/vapi_scam_caller.py"}
            r = await client.get(f"/phone-number/{os.environ['VAPI_PHONE_NUMBER_ID']}")
            guard_number = r.json().get("number")
            r = await client.post("/call", json={"assistantId": aid, "customer": {"number": guard_number},
                                                 "phoneNumberId": os.environ["VAPI_SCAM_PHONE_ID"]})
            if r.status_code >= 300:
                msg = r.json().get("message") if "json" in r.headers.get("content-type", "") else None
                return {"started": False, "reason": f"Vapi refused ({r.status_code}): {msg}"[:200]}
            call = r.json()
    except Exception:
        return {"started": False, "reason": "Vapi request failed"}  # never echo exceptions: they can hold keys
    _scam.clear()
    _scam.update(id=call["id"], control_url=(call.get("monitor") or {}).get("controlUrl"), t=time.time())
    return {"started": True, "vapi_call_id": call["id"]}


@router.post("/sim/scam-call/stop")
async def scam_call_stop():
    if not _scam:
        return {"stopped": False, "reason": "no scam call"}
    ok = False
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            if _scam.get("control_url"):
                ok = (await client.post(_scam["control_url"], json={"type": "end-call"})).status_code < 300
    except Exception:
        ok = False
    cid = _scam["id"]
    _scam.clear()
    return {"stopped": ok, "vapi_call_id": cid}


if __name__ == "__main__":
    secret = os.environ.get("APP_SECRET", "")
    if not secret:
        raise SystemExit("APP_SECRET is required")
    base = os.environ.get("PUBLIC_BASE_URL") or "http://localhost:8000"
    print(f"{base.rstrip('/')}/#k={_sim_key(secret)}")
