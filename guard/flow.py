import asyncio

from guard import credential, persona, settings, state, telegram, vapi
from guard.brain import classify, contacts

_tasks: set = set()
_caller_text: dict[str, str] = {}
_asked_name: set[str] = set()
_stepups: set[str] = set()
_late_denials: set[str] = set()


def spawn(coro) -> None:
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


def start_call(caller_number, call_id=None) -> str:
    call = state.create_call(caller_number, call_id)
    digits = "".join(c for c in (caller_number or "") if c.isdigit())
    state.emit(call.call_id, "call.started", {"caller_number": f"•••• {digits[-4:]}" if digits else None})
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
    if call is None or call.state != "screening":
        return
    claim = classify.claim(_caller_text.get(call_id, ""))
    claim.claimed_name = claim.claimed_name or claimed_name
    state.update_call(call_id, claimed_name=claim.claimed_name)
    found = contacts.candidates(claim, call.caller_number)
    # A relationship alone never supplies a name to the caller, even with one match.
    if found and (len(found) > 1 or not claim.claimed_name):
        if call_id not in _asked_name:
            _asked_name.add(call_id)
            text = "Ask the caller for their first name. Do not offer any name."
            vapi.add_message(call_id, text)
            state.emit(call_id, "action", {"kind": "ask_name", "text": text})
            return
        found = []
    lk = found[0] if found else contacts.lookup(None, call.caller_number)
    shown = {"id": lk.contact.id, "name": lk.contact.name, "relationship": lk.contact.relationship} if lk.contact else None
    state.emit(call_id, "contact.lookup", {"contact": shown, "number_known": lk.number_known, "note": lk.note})
    if lk.contact and lk.number_known and call.flags is not None and not call.flags.needs_verification:
        state.update_call(call_id, contact_id=lk.contact.id)
        if await _resolve(call_id, "confirmed", "known_number"):
            if settings.REAL_TELEGRAM:
                spawn(asyncio.to_thread(telegram.send_notice, state.get_call(call_id), lk.contact))
            else:
                telegram.send_notice(state.get_call(call_id), lk.contact)
            state.emit(call_id, "notice.sent", {
                "contact_id": lk.contact.id, "channels": ["telegram"],
                "text": f"You are calling {settings.OWNER_NAME} right now. Not you? Tap below.",
            })
        return
    if not state.set_pending(call_id, lk.contact.id if lk.contact else None):
        return
    if lk.contact is None:
        await _resolve(call_id, "unverified", "lookup")
        return
    if contacts.denied_today(lk.contact.id, call.caller_number):
        await _resolve(call_id, "denied", "repeat_attempt")
        return
    telegram.send_ping(state.get_call(call_id), lk.contact, claim)
    text = telegram.ping_text(lk.contact, claim)
    state.emit(call_id, "ping.sent", {"contact_id": lk.contact.id, "channels": ["telegram"], "text": text})
    spawn(_timeout(call_id))


async def _timeout(call_id) -> None:
    await asyncio.sleep(settings.TIMEOUT_S)
    await _resolve(call_id, "unverified", "timeout")


async def on_tap(call_id, answer) -> bool:
    call = state.get_call(call_id)
    if call is None:
        return False
    if (answer == "no" and call.state == "confirmed" and call.decided_by == "known_number"
            and call_id not in _late_denials):
        _late_denials.add(call_id)
        contact = contacts.get(call.contact_id)
        name = contact.name if contact else "the caller"
        text = f"That call was not {name}. Don't send money."
        state.emit(call_id, "action", {"kind": "late_denial", "text": text})
        vapi.hangup(call_id)
        await _notify(call_id, text)
        return True
    if call.state != "pending":
        return False
    if answer == "yes" and call_id in _stepups:
        return False
    if answer == "yes" and call.flags and call.flags.needs_verification:
        try:
            link = persona.start(call.contact_id)
        except Exception:
            link = None
        if not link and settings.REAL_PERSONA and not settings.FAKE:
            return await _resolve(call_id, "unverified", "stepup_unavailable")
        if link:
            _stepups.add(call_id)
            state.emit(call_id, "action", {"kind": "stepup", "link": link})
            return True
    new_state = {"yes": "confirmed", "no": "denied"}.get(answer)
    return bool(new_state) and await _resolve(call_id, new_state, "tap")


async def on_stepup(call_id, ok: bool) -> bool:
    if call_id not in _stepups:
        return False
    _stepups.discard(call_id)
    return await _resolve(call_id, "confirmed" if ok else "denied", "stepup")


async def on_credential(call_id, token) -> bool:
    info = credential.check(token)
    if info is None:
        return False
    call = state.get_call(call_id)
    if call is None or call.state not in ("screening", "pending"):
        return False
    if call.flags and call.flags.money and "payment" not in info.get("scope", []):
        state.emit(call_id, "action", {"kind": "out_of_scope", "text": "Credential does not allow payment."})
        return False
    state.emit(call_id, "credential.verified", info)
    return await _resolve(call_id, "agent_verified", "credential")


async def _resolve(call_id, new_state, by) -> bool:
    if not state.decide(call_id, new_state, by):
        return False  # someone already decided: do nothing
    c = state.get_call(call_id)
    _stepups.discard(call_id)
    contacts.learn(c)
    secs = round((c.decided_at - c.started_at).total_seconds(), 1)
    state.emit(call_id, "decision", {"state": new_state, "by": by, "seconds_to_decide": secs})
    await _act(call_id, new_state)
    return True


async def _act(call_id, new_state) -> None:
    """Only the winner of decide() gets here; late denials also hang up in on_tap."""
    c = state.get_call(call_id)
    contact = contacts.get(c.contact_id) if c.contact_id else None
    name = contact.name if contact else "the caller"
    if new_state == "denied":
        vapi.say(call_id, "This call is not who it claims to be. Goodbye.")
        vapi.hangup(call_id)
        text = (f"Repeat attempt as {name} today. Don't send money."
                if c.decided_by == "repeat_attempt" else f"That wasn't {name}. Don't send money.")
        await _notify(call_id, text)
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
