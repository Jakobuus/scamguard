import asyncio
import hmac
import os
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from guard import flow, settings


def _sim_key(secret: str) -> str:
    return hmac.new(secret.encode(), b"sim-key", "sha256").hexdigest()[:20]


def _authorize(req: Request) -> None:
    if settings.FAKE:
        return
    secret = os.environ.get("APP_SECRET", "")
    key = req.headers.get("X-Sim-Key", "")
    if not secret or not hmac.compare_digest(key.encode(), _sim_key(secret).encode()):
        raise HTTPException(403, "Invalid sim key")


router = APIRouter(dependencies=[Depends(_authorize)])
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


if __name__ == "__main__":
    secret = os.environ.get("APP_SECRET", "")
    if not secret:
        raise SystemExit("APP_SECRET is required")
    base = os.environ.get("PUBLIC_BASE_URL") or "http://localhost:8000"
    print(f"{base.rstrip('/')}/#k={_sim_key(secret)}")
