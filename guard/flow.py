import asyncio
import hmac
import re
import time
from datetime import datetime, timezone

from guard import channels, credential, settings, state, telegram, vapi
from guard.brain import classify, contacts, context, scripts
from guard.brain.redact import redact

_tasks: set = set()
_caller_text: dict[str, str] = {}
_asked_name: set[str] = set()
_verification_started: set[str] = set()
_caller_activity: dict[str, float] = {}
_net_tasks: dict[str, asyncio.Task] = {}
_late_warned: set[str] = set()
_MONEY_RE = (r"\b(fees?|gift cards?|payment|pay|bank|credit card|debit card|wire|transfer|cash|"
             r"crypto|bitcoin|dollars?|customs|deposit|card number)\b|\$")
_late_denials: set[str] = set()
_code_tries: dict[str, int] = {}
_pings: dict[tuple, int] = {}
_cap_notified: set[tuple] = set()
# Live (partial) transcript lines: memory only, never stored or emitted as events.
PARTIAL_TTL_S = 4.0
_partials: dict[str, dict[str, dict]] = {}  # call_id -> role -> {"text", "ts"}
_last_final: dict[tuple, tuple[str, float]] = {}  # (call_id, role) -> (normalized text, time)
_live_calls: dict[str, None] = {}  # started and not yet ended, oldest first


def reset_demo() -> None:
    contacts.reset_demo()
    _pings.clear()
    _cap_notified.clear()
    _caller_text.clear()
    _verification_started.clear()
    _caller_activity.clear()
    _net_tasks.clear()
    _late_warned.clear()
    scripts.reset()


def _normalize(text):
    return " ".join(re.findall(r"\w+", text.casefold()))


def _redact(text):
    for contact in contacts._all():
        words = re.findall(r"\w+", contact.challenge_answer)
        if words:
            text = re.sub(r"(?<!\w)" + r"[\W_]+".join(map(re.escape, words)) + r"(?!\w)",
                          "••••", text, flags=re.IGNORECASE)
            expected = _normalize(contact.challenge_answer)
            text = re.sub(r"\w+", lambda m: "••••" if contacts.close(_normalize(m[0]), expected)
                          else m[0], text)
    return text


def _drop_code_word_prefix(text):
    """A half-spoken code word must not show: drop trailing words that begin a code word."""
    words = text.split()
    for contact in contacts._all():
        secret = " ".join(re.findall(r"\w+", contact.challenge_answer.casefold()))
        if not secret:
            continue
        for k in range(min(len(words), len(secret.split())), 0, -1):
            tail = " ".join(_normalize(w) for w in words[-k:]).strip()
            if len(tail.replace(" ", "")) >= 3 and secret.startswith(tail):
                words = words[:-k]
                break
    return " ".join(words)


def _code_word_eligible(call_id):
    call = state.get_call(call_id)
    return call and call.contact_id and call.state in ("pending", "unverified")


def on_partial(call_id, role, text) -> None:
    """Keep only the latest in-progress words per call and role, masked like finals."""
    if role == "caller":
        _caller_activity[call_id] = time.time()
    if role == "caller" and _code_word_eligible(call_id):
        _partials.get(call_id, {}).pop(role, None)
        return
    text = _drop_code_word_prefix(_redact(text)).strip()
    now = time.time()
    final = _last_final.get((call_id, role))
    if final and now - final[1] < 1.0 and final[0].startswith(_normalize(text)):
        return  # a late partial of the line that just became final
    text = redact(text, rules_only=True, partial=True).text
    if not text:
        _partials.get(call_id, {}).pop(role, None)
        return
    _partials.setdefault(call_id, {})[role] = {"text": text, "ts": now}


def live() -> dict:
    """Latest partial lines of the most recent active call. Memory only, no database."""
    calls = list(_live_calls)
    call_id = calls[-1] if calls else None
    out = {"call_id": call_id, "caller": None, "agent": None}
    parts = _partials.get(call_id, {}) if call_id else {}
    now = time.time()
    for role in ("caller", "agent"):
        if role == "caller" and call_id and _code_word_eligible(call_id):
            parts.pop(role, None)
            continue
        p = parts.get(role)
        if p and now - p["ts"] <= PARTIAL_TTL_S:
            out[role] = dict(p)
        elif p:
            parts.pop(role, None)
    return out


def track_live(event) -> None:
    """State subscriber: a call stops being live when it ends."""
    if event.type == "call.ended":
        _live_calls.pop(event.call_id, None)
        _partials.pop(event.call_id, None)


def mark_live(call_id) -> None:
    _live_calls.pop(call_id, None)
    _live_calls[call_id] = None
    while len(_live_calls) > 50:
        old = next(iter(_live_calls))
        _live_calls.pop(old, None)
        _partials.pop(old, None)


def spawn(coro) -> None:
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


def start_call(caller_number, call_id=None) -> str:
    call = state.create_call(caller_number, call_id)
    digits = "".join(c for c in (caller_number or "") if c.isdigit())
    mark_live(call.call_id)
    state.emit(call.call_id, "call.started", {"caller_number": f"•••• {digits[-4:]}" if digits else None})
    return call.call_id


async def on_transcript(call_id, role, text) -> None:
    text = _redact(text)
    _partials.get(call_id, {}).pop(role, None)
    _last_final[(call_id, role)] = (_normalize(text), time.time())
    if len(_last_final) > 200:
        _last_final.pop(next(iter(_last_final)))
    state.emit(call_id, "transcript", {"role": role, "text": text})
    if role == "caller":
        _caller_text[call_id] = (_caller_text.get(call_id, "") + " " + text).strip()
        f = classify.flags(_caller_text[call_id])
        state.update_call(call_id, flags=f)
        state.emit(call_id, "flags", f.model_dump())
        spawn(scripts.on_text(call_id, _caller_text[call_id], f))  # fire-and-forget, never blocks the call
        _caller_activity[call_id] = time.time()
        call = state.get_call(call_id)
        if (call and call.state == "screening" and call_id not in _verification_started):
            task = _net_tasks.get(call_id)
            if task is None or task.done():  # one pending task per call; new words only move its clock
                _net_tasks[call_id] = asyncio.create_task(_expected_net(call_id))
                _tasks.add(_net_tasks[call_id])
                _net_tasks[call_id].add_done_callback(_tasks.discard)
        elif (call and call.state == "confirmed" and call.decided_by == "expected"
                and call_id not in _late_warned
                and (f.money or f.urgency or f.secrecy or re.search(_MONEY_RE, text, re.I))):
            _late_warned.add(call_id)
            state.emit(call_id, "action", {"kind": "late_warning"})
            await _notify(call_id, "The caller you were expecting is now asking for money. Don't pay; hang up.")


def verification_started(call_id) -> None:
    _verification_started.add(call_id)


async def _expected_net(call_id) -> None:
    """Transcript safety net: one task per call. Money shows -> act at once; otherwise wait for caller quiet."""
    while True:
        await asyncio.sleep(0.1)
        call = state.get_call(call_id)
        if (not call or call.state != "screening" or call_id in _verification_started
                or call_id not in _live_calls):
            return
        text = _caller_text.get(call_id, "")
        money = bool((call.flags and call.flags.money) or re.search(_MONEY_RE, text, re.I))
        quiet = time.time() - _caller_activity.get(call_id, 0) >= settings.EXPECTED_QUIET_S
        if money or quiet:  # a money request never needs the wait: it can only block, never forward
            await _expected_without_tool(call_id)
            return


async def _expected_without_tool(call_id) -> None:
    call = state.get_call(call_id)
    text = _caller_text.get(call_id, "")
    def circle_claim():
        claim = classify.claim(text)
        claim.claimed_name = _caller_name(claim.claimed_name, text)
        return contacts.candidates(claim, call.caller_number)
    found = await asyncio.to_thread(circle_claim)
    if text != _caller_text.get(call_id, ""):
        return
    await _expected_call(call_id, found, without_tool=True)


async def _expected_call(call_id, found, without_tool=False) -> bool:
    if found:
        return False
    text = _caller_text.get(call_id, "")
    expectation = await asyncio.to_thread(context.expected, text)
    if without_tool and (expectation is None or call_id in _verification_started
                         or call_id not in _live_calls or text != _caller_text.get(call_id, "")):
        return False
    generic = expectation is None and any(
        record.get("expected", True) and any(context._contains(term, text)
                                            for term in record["keywords"]["generic"])
        for record in context.records()
    )
    # A courier or caller who names no item: still a delivery-like call, so take a message.
    delivery_words = expectation is None and not generic and bool(re.search(
        r"\b(deliver\w*|parcels?|packages?|couriers?|orders?|appointments?|callbacks?|doctors?|pharmacy)\b",
        text, re.I))
    if expectation is None and not generic and not delivery_words:
        return False
    def pressure():
        call = state.get_call(call_id)
        if call is None or call.state != "screening":
            return call, False, False
        latest = _caller_text.get(call_id, "")
        money = bool((call.flags and call.flags.money) or re.search(_MONEY_RE, latest, re.I))
        blocked = (call.flags is None or money or call.flags.urgency or call.flags.secrecy
                   or call.flags.needs_verification)
        return call, money, blocked

    call, money, blocked = pressure()  # Flags may have changed during retrieval.
    if call is None or call.state != "screening":
        return True
    if expectation and not blocked and not without_tool:
        # HACK: the tool's summary can arrive before the caller's money words; wait, then decide on the latest.
        await asyncio.sleep(0.75)
        call, money, blocked = pressure()
        if call is None or call.state != "screening":
            return True
    if delivery_words and blocked:
        return False  # Pressure or money: ordinary unverified path.
    if expectation is None and money:
        return False  # Generic money claims retain the ordinary unverified path.
    state.emit(call_id, "contact.lookup", {"contact": None, "number_known": False,
               "note": "Not someone from the circle."})
    if expectation:
        state.emit(call_id, "context.expected", expectation.model_dump())
    # Both backends already provide the atomic screening -> pending -> decision
    # path. Reserve it without a ping, keeping state backend ownership intact.
    if not state.set_pending(call_id, None):
        return True
    if expectation is None or (blocked and not money):
        # Resolve atomically without _act's identity challenge and money warning.
        if state.decide(call_id, "unverified", "lookup"):
            call = state.get_call(call_id)
            contacts.learn(call)
            state.emit(call_id, "decision", {"state": "unverified", "by": "lookup",
                       "seconds_to_decide": round((call.decided_at - call.started_at).total_seconds(), 1)})
            instruction = "Take a short message for the owner. Do not put the caller through."
            vapi.add_message(call_id, instruction)
            state.emit(call_id, "action", {"kind": "take_message", "text": instruction})
            mentioned = f"your {expectation.who}" if expectation else "a delivery or appointment"
            await _notify(call_id, f"A caller mentioned {mentioned}. I took a message instead of putting them through.")
    elif money:
        state.emit(call_id, "action", {"kind": "expected_but_money"})
        if await _resolve(call_id, "unverified", "expected_but_money"):
            await _notify(call_id, f"Caller claims to be your {expectation.kind} but asks for money. Don't pay.")
    else:
        if await _resolve(call_id, "confirmed", "expected"):
            why = expectation.why[:1].lower() + expectation.why[1:]
            await _notify(call_id, f"Expected: {why} Putting the call through.")
    return True


_ADDRESS = {"mum", "mom", "mummy", "mommy", "mama", "mother", "dad", "daddy", "papa", "father",
            "grandma", "grandpa", "granny", "nana"}


def _caller_name(name, text):
    """The person being called is never the claimed person: drop "Mum", the owner's name and the like.
    When the extractor took the addressee, fall back to the keyword name ("it's Jakob")."""
    def own(n):
        n = re.sub(r"[\W_]+$", "", (n or "").strip()).casefold()
        if any(n == known.casefold() for c in contacts._all() for known in [c.name, *c.nicknames]):
            return False  # a contact may really be called "Dad" or "Nana"
        return n in _ADDRESS or n == settings.OWNER_NAME.casefold()
    if not name or not own(name):
        return name
    fallback = classify._fake_claim(text).claimed_name
    return None if not fallback or own(fallback) else fallback


async def verify_caller(call_id, claimed_name, reason=None) -> None:
    verification_started(call_id)
    call = state.get_call(call_id)
    if call is None or call.state != "screening":
        return
    if isinstance(reason, str) and reason.strip():
        _caller_text[call_id] = (_caller_text.get(call_id, "") + " " + _redact(reason)).strip()
        f = classify.flags(_caller_text[call_id])
        state.update_call(call_id, flags=f)
        state.emit(call_id, "flags", f.model_dump())
    claim = classify.claim(_caller_text.get(call_id, ""))
    claim.claimed_name = _caller_name(claim.claimed_name, _caller_text.get(call_id, "")) or claimed_name
    state.emit(call_id, "claim", {**claim.model_dump(), "asks_for": telegram.asks_for(claim)})
    state.update_call(call_id, claimed_name=claim.claimed_name)
    found = contacts.candidates(claim, call.caller_number)
    if await _expected_call(call_id, found):
        return
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
                "contact_id": lk.contact.id, "channels": ["telegram"], "timeout_s": None,
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
    key = (datetime.now(timezone.utc).date(), lk.contact.id)
    if _pings.get(key, 0) >= 5:
        await _resolve(call_id, "unverified", "ping_cap")
        if key not in _cap_notified:
            _cap_notified.add(key)
            await _notify(call_id, f"Many attempts as {lk.contact.name} today. Don't send money.")
        return
    _pings[key] = _pings.get(key, 0) + 1
    asked = channels.send_ping(state.get_call(call_id), lk.contact, claim)
    text = telegram.ping_text(lk.contact, claim)
    state.emit(call_id, "ping.sent", {"contact_id": lk.contact.id, "channels": asked, "timeout_s": settings.TIMEOUT_S, "text": text})
    spawn(_timeout(call_id))


async def _timeout(call_id) -> None:
    await asyncio.sleep(settings.TIMEOUT_S)
    await _resolve(call_id, "unverified", "timeout")


async def on_tap(call_id, answer, channel="telegram") -> bool:
    call = state.get_call(call_id)
    if call is None:
        return False
    if (answer == "no" and call.state == "confirmed" and call.decided_by in ("known_number", "code_word")
            and call_id not in _late_denials):
        _late_denials.add(call_id)
        contact = contacts.get(call.contact_id)
        name = contact.name if contact else "the caller"
        text = f"That call was not {name}. Don't send money."
        state.emit(call_id, "action", {"kind": "late_denial", "text": text})
        vapi.hangup(call_id)
        await _notify(call_id, text)
        return True
    # HACK: a No that arrives after the timeout still ends the call (unless the code-word path already hung up).
    late_no = answer == "no" and call.state == "unverified" and call.decided_by == "timeout"
    if call.state != "pending" and not late_no:
        return False
    new_state = {"yes": "confirmed", "no": "denied"}.get(answer)
    return bool(new_state) and await _resolve(call_id, new_state, "tap", channel=channel)


async def on_code_word(call_id, spoken: str) -> str:
    call = state.get_call(call_id)
    contact = contacts.get(call.contact_id) if call and call.contact_id else None
    if (not contact or call.state not in ("pending", "unverified")
            or _code_tries.get(call_id, 0) >= 2):
        return "failed"
    _code_tries[call_id] = _code_tries.get(call_id, 0) + 1
    expected, supplied = _normalize(contact.challenge_answer), _normalize(spoken)
    # Speech-to-text may spell the word slightly differently: allow one edit for 5+ letters.
    if expected and (hmac.compare_digest(supplied.encode(), expected.encode())
                     or re.search(r"(?<!\w)" + re.escape(expected) + r"(?!\w)", supplied)
                     or any(contacts.close(w, expected) for w in supplied.split())):
        if not await _resolve(call_id, "confirmed", "code_word"):
            return "failed"
        c = state.get_call(call_id)
        if settings.REAL_TELEGRAM:
            spawn(asyncio.to_thread(telegram.send_notice, c, contact))
        else:
            telegram.send_notice(c, contact)
        state.emit(call_id, "notice.sent", {
            "contact_id": contact.id, "channels": ["telegram"], "timeout_s": None,
            "text": f"Someone reached {settings.OWNER_NAME} with your code word. Not you? Tap below.",
        })
        return "ok"
    if _code_tries[call_id] < 2:
        return "retry"
    # No await between eligibility and the decision; a winning tap cannot be overridden.
    if call.state == "pending" and not await _resolve(call_id, "unverified", "code_word"):
        return "failed"
    state.emit(call_id, "action", {"kind": "code_word_failed"})
    vapi.hangup(call_id)
    await _notify(call_id, "Unverified caller failed the code word. Don't send money.")
    return "failed"


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


async def _resolve(call_id, new_state, by, channel=None) -> bool:
    if not state.decide(call_id, new_state, by):
        return False  # someone already decided: do nothing
    c = state.get_call(call_id)
    contacts.learn(c)
    secs = round((c.decided_at - c.started_at).total_seconds(), 1)
    state.emit(call_id, "decision", {"state": new_state, "by": by, "seconds_to_decide": secs,
                                    **({"channel": channel} if by == "tap" else {})})
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
        if c.decided_by == "code_word":
            return  # on_code_word handles the final failure.
        q = ("No answer from the real person yet. Say: 'I could not reach her. If you know the family code "
             "word, please say it now.' Then listen, and pass exactly what the caller says to check_code_word. "
             "Do not hint at the word."
             if contact else "Who are you and why are you calling?")
        vapi.add_message(call_id, q)
        if c.decided_by != "ping_cap":
            await _notify(call_id, "Unverified. Don't send money.")


async def _notify(call_id, text) -> None:
    if not text.startswith("Expected:"):
        text += scripts.warning(call_id)
    telegram.notify_owner(text)
    state.emit(call_id, "owner.notified", {"text": text})
