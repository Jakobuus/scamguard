import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from guard import flow

router = APIRouter()
_sim_calls: set[str] = set()  # /sim/tap may only decide calls that /sim/call started, never a real one


class SimCall(BaseModel):
    claimed_name: str | None = None
    caller_number: str | None = None
    lines: list[str] = []
    tap: Literal["yes", "no"] | None = None
    tap_after_s: float = 1.0
    credential: str | None = None  # extra: plays the credential path


class SimTap(BaseModel):
    call_id: str
    answer: Literal["yes", "no"]


async def _play(call_id: str, b: SimCall) -> None:
    for line in b.lines:
        await flow.on_transcript(call_id, "caller", line)
        await asyncio.sleep(0.3)
    if b.credential:
        await flow.on_credential(call_id, b.credential)
        return
    await flow.verify_caller(call_id, b.claimed_name)
    if b.tap:
        await asyncio.sleep(b.tap_after_s)
        await flow.on_tap(call_id, b.tap)


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
