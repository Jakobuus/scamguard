import asyncio

from guard import credential, persona, settings, state, telegram, vapi
from guard.brain import classify, contacts

_tasks: set = set()
_caller_text: dict[str, str] = {}


def spawn(coro) -> None:
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


def start_call(caller_number, call_id=None) -> str:
    call = state.create_call(caller_number, call_id)
    state.emit(call.call_id, "call.started", {"caller_number": caller_number})
    return call.call_id


async def on_transcript(call_id, role, text) -> None:
    state.emit(call_id, "transcript", {"role": role, "text": text})
    if role == "caller":
        _caller_text[call_id] = (_caller_text.get(call_id, "") + " " + text).strip()
        f = classify.flags(_caller_text[call_id])
        state.update_call(call_id, flags=f)
        state.emit(call_id, "flags", f.model_dump())


async def verify_caller(call_id, claimed_name) -> None:
    call = state.get_call(call_id)
    state.update_call(call_id, claimed_name=claimed_name)
    lk = contacts.lookup(claimed_name, call.caller_number)
    if not state.set_pending(call_id, lk.contact.id if lk.contact else None):
        return  # already pending or decided: one ping per call
    # the screen is public: only name and relationship leave the server, never numbers or chat ids
    shown = {"id": lk.contact.id, "name": lk.contact.name, "relationship": lk.contact.relationship} if lk.contact else None
    state.emit(call_id, "contact.lookup", {"contact": shown, "number_known": lk.number_known, "note": lk.note})
    if lk.contact is None:
        await _resolve(call_id, "unverified", "lookup")
        return
    telegram.send_ping(state.get_call(call_id), lk.contact)
    state.emit(call_id, "ping.sent", {"contact_id": lk.contact.id, "channels": ["telegram"]})
    spawn(_timeout(call_id))


async def _timeout(call_id) -> None:
    await asyncio.sleep(settings.TIMEOUT_S)
    await _resolve(call_id, "unverified", "timeout")


async def on_tap(call_id, answer) -> bool:
    state_ = {"yes": "confirmed", "no": "denied"}.get(answer)
    return bool(state_) and await _resolve(call_id, state_, "tap")


async def on_credential(call_id, token) -> bool:
    info = credential.check(token)
    if info is None:
        return False
    state.emit(call_id, "credential.verified", info)
    return await _resolve(call_id, "agent_verified", "credential")


async def _resolve(call_id, new_state, by) -> bool:
    if not state.decide(call_id, new_state, by):
        return False  # someone already decided: do nothing
    c = state.get_call(call_id)
    secs = round((c.decided_at - c.started_at).total_seconds(), 1)
    state.emit(call_id, "decision", {"state": new_state, "by": by, "seconds_to_decide": secs})
    await _act(call_id, new_state)
    return True


async def _act(call_id, new_state) -> None:
    """Only the winner of decide() gets here. The only place that hangs up or forwards."""
    c = state.get_call(call_id)
    contact = contacts.get(c.contact_id) if c.contact_id else None
    name = contact.name if contact else "the caller"
    if new_state == "denied":
        vapi.say(call_id, "This call is not who it claims to be. Goodbye.")
        vapi.hangup(call_id)
        await _notify(call_id, f"That wasn't {name}. Don't send money.")
        state.emit(call_id, "call.ended", {})
    elif new_state in ("confirmed", "agent_verified"):
        vapi.forward(call_id)
    elif new_state == "unverified":
        q = contact.challenge_question if contact else "Who are you and why are you calling?"
        vapi.add_message(call_id, q)
        await _notify(call_id, "Unverified. Don't send money.")


async def _notify(call_id, text) -> None:
    telegram.notify_owner(text)
    state.emit(call_id, "owner.notified", {"text": text})
