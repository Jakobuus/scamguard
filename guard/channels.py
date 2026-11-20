"""Non-blocking question delivery; all answers share the flow's atomic decision."""
import asyncio
import html
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from guard import settings, state, telegram

log = logging.getLogger("guard.channels")
router = APIRouter()


ACCOUNT_PATH = Path(__file__).resolve().parent.parent / "data" / "account.local.json"


def targets(contact=None) -> dict[str, str | None]:
    """The one place that decides where a contact is asked.

    A contact's own targets (own fields, usable account handles) are used alone. The global
    fallbacks (TELEGRAM_CHAT_ID, BEEPER_CHAT_ID, PING_EMAIL_TO) apply only to a contact with
    no own target at all, so a question about one person never reaches someone else's phone.
    """
    fallback = {
        "telegram": os.environ.get("TELEGRAM_CHAT_ID"),
        "whatsapp": os.environ.get("BEEPER_CHAT_ID"),
        "email": os.environ.get("PING_EMAIL_TO"),
    }
    if contact is None:
        return fallback
    own = {kind: v for kind, v in (
        ("telegram", getattr(contact, "telegram_chat_id", None)),
        ("whatsapp", getattr(contact, "whatsapp_chat_id", None)),
        ("email", getattr(contact, "email", None))) if v}
    disabled = set()
    try:
        data = json.loads(ACCOUNT_PATH.read_text())
        entries = data["contacts"][contact.id]["channels"]
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            kind, handle = entry.get("kind"), entry.get("handle")
            if not isinstance(kind, str) or kind not in fallback:
                continue
            if entry.get("enabled") is False:
                disabled.add(kind)
            elif isinstance(handle, str):
                handle = handle.strip()
                if ((kind == "whatsapp" and handle.startswith("!"))
                        or (kind == "telegram" and re.fullmatch(r"-?[0-9]+", handle))
                        or (kind == "email" and re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", handle))):
                    own[kind] = handle
    except (OSError, ValueError, TypeError, KeyError):
        pass
    resolved = own or fallback
    for kind in disabled:
        resolved = {k: v for k, v in resolved.items() if k != kind}
    return resolved


def enabled(contact=None, *, resolved=None) -> list[str]:
    resolved = targets(contact) if resolved is None else resolved
    # HACK: Telegram keeps its existing fake ping even without bot configuration.
    names = ["telegram"] if "telegram" in resolved else []
    for name, flag, required in (
        ("whatsapp", "REAL_BEEPER", ("BEEPER_ACCESS_TOKEN",)),
        ("email", "REAL_EMAIL", ("RESEND_API_KEY", "PUBLIC_BASE_URL", "APP_SECRET")),
    ):
        if resolved.get(name) and settings._flag(flag) and all(os.environ.get(key) for key in required):
            names.append(name)
    return names


def beeper_request(method, path, timeout=3, **kwargs):
    response = httpx.request(
        method, (os.environ.get("BEEPER_BASE_URL") or "http://localhost:23373").rstrip("/") + path,
        headers={"Authorization": "Bearer " + os.environ.get("BEEPER_ACCESS_TOKEN", "")},
        timeout=timeout, **kwargs,
    )
    response.raise_for_status()
    return response.json()


def message_path(chat_id):
    return "/v1/chats/" + quote(chat_id, safe="") + "/messages"


def reply_answer(text):
    text = (text or "").strip()
    if text in ("\U0001F44E", "\U0001F44D"):
        return "no" if text == "\U0001F44E" else "yes"
    first = text.split(maxsplit=1)
    word = first[0].strip(".,!?;:\"'\u201c\u201d\u2018\u2019").casefold() if first else ""
    if word in ("no", "n", "nope", "nein", "nah", "not"):
        return "no"
    if word in ("yes", "y", "ja", "yeah", "yep", "yup"):
        return "yes"
    return None


_latest: dict[str, str] = {}


def result_text(answer, won):
    if not won:
        return "Too late, already decided."
    return "Thanks. Putting the call through." if answer == "yes" else "Thanks. Call ended."


OWN_PREFIXES = ("someone is calling", "thanks.", "too late", "call guard test", "test,", "someone reached", "you are calling")


def _norm(text):
    return " ".join((text or "").replace("<br>", " ").replace("<br/>", " ").split()).casefold()


def _own_line(text):
    return _norm(text).startswith(OWN_PREFIXES)


def _why(e):
    # Never log URLs, headers or HTTP bodies: they can carry credentials.
    return type(e).__name__


async def _whatsapp(call, text, chat_id=None, notice=False):
    if not settings.REAL_BEEPER:
        log.info("fake whatsapp ping (call %s): %s", call.call_id, text)
        return
    try:
        chat_id = chat_id or os.environ["BEEPER_CHAT_ID"]
        path = message_path(chat_id)
        # One line: the listed text of a multi-line message comes back with <br>.
        question = text if notice else text + " Reply NO or YES."
        _latest[chat_id] = call.call_id
        before = await asyncio.to_thread(beeper_request, "GET", path)
        seen = {m.get("id") for m in before.get("items", [])}
        try:
            # Sending can take longer than a poll; a timeout does not mean it was not sent.
            await asyncio.to_thread(beeper_request, "POST", path, timeout=15, json={"text": question})
        except httpx.TimeoutException:
            log.warning("WhatsApp send timed out; polling for the reply anyway")
        own_sender = None
        deadline = time.monotonic() + 90
        # HACK: poll only the latest page; the demo chat has low message volume.
        while time.monotonic() < deadline:
            if _latest.get(chat_id) != call.call_id:
                return
            current = state.get_call(call.call_id)
            if not current or current.state not in (("confirmed",) if notice else ("pending", "unverified")):
                return
            await asyncio.sleep(1)
            if _latest.get(chat_id) != call.call_id:
                return
            try:
                page = await asyncio.to_thread(beeper_request, "GET", path)
            except httpx.HTTPError as e:
                log.warning("WhatsApp poll failed: %s", _why(e))
                continue
            items = page.get("items", [])
            for message in reversed(items):  # oldest first
                try:
                    mid, body = message.get("id"), message.get("text") or ""
                    if mid in seen:
                        continue
                    seen.add(mid)
                    # isSender is not used: in bridged chats every message can carry isSender true.
                    # Our own question is identified by its text; the prefix filter is a second guard.
                    if _norm(body) == _norm(question):
                        own_sender = message.get("senderID")
                        continue
                    if (_own_line(body) or message.get("isDeleted")
                            or message.get("type", "TEXT") != "TEXT"):
                        continue
                    answer = reply_answer(body)
                    if notice and answer != "no":
                        continue  # a notice only listens for a late denial
                    if answer:
                        if _latest.get(chat_id) != call.call_id:
                            return
                        if message.get("senderID") and own_sender:
                            log.info("WhatsApp reply same_sender=%s", str(message["senderID"] == own_sender).lower())
                        from guard import flow
                        won = await flow.on_tap(call.call_id, answer, channel="whatsapp")
                        await asyncio.to_thread(beeper_request, "POST", path, timeout=15,
                                                json={"text": result_text(answer, won)})
                        return
                except Exception:
                    log.warning("Skipped one bad WhatsApp message")
    except Exception as e:
        log.warning("WhatsApp question failed: %s", _why(e))


def _email(call, text, recipient=None):
    if not settings.REAL_EMAIL:
        log.info("fake email ping (call %s): %s", call.call_id, text)
        return
    links = []
    for action, label in (("n", "No, end the call"), ("y", "Yes, it's me")):
        url = os.environ["PUBLIC_BASE_URL"].rstrip("/") + "/answer?" + urlencode(
            {"d": telegram.callback_data(action, call.call_id)})
        links.append(f'<p><a style="display:block;padding:24px;font-size:24px" href="{html.escape(url)}">{html.escape(label)}</a></p>')
    response = httpx.post("https://api.resend.com/emails", timeout=3,
                          headers={"Authorization": "Bearer " + os.environ["RESEND_API_KEY"],
                                   "User-Agent": "curl/8.7.1"},
                          json={"from": os.environ.get("PING_EMAIL_FROM") or "Call Guard <onboarding@resend.dev>",
                                "to": [recipient or os.environ["PING_EMAIL_TO"]], "subject": text,
                                "html": f"<p>{html.escape(text)}</p>" + "".join(links)})
    response.raise_for_status()


async def _deliver(name, call, contact, claim, target=None):
    if target is None:
        target = targets(contact).get(name)
    try:
        if name == "telegram":
            await asyncio.to_thread(telegram.send_ping, call, contact.model_copy(update={"telegram_chat_id": target}), claim)
        elif name == "whatsapp":
            await _whatsapp(call, telegram.ping_text(contact, claim), target)
        else:
            await asyncio.to_thread(_email, call, telegram.ping_text(contact, claim), target)
    except Exception as e:
        log.warning("Question delivery failed on %s: %s", name, _why(e))


def send_notice(call, contact):
    """The "Not me" notice, on the contact's own Telegram and WhatsApp targets. Returns the channels used."""
    from guard import flow
    resolved = targets(contact)
    names = [n for n in enabled(contact, resolved=resolved) if n in ("telegram", "whatsapp")]
    if not names:
        log.info("no channel for notice")
    for name in names:
        if name == "telegram":
            notified = contact.model_copy(update={"telegram_chat_id": resolved["telegram"]})
            if settings.REAL_TELEGRAM:
                flow.spawn(asyncio.to_thread(telegram.send_notice, call, notified))
            else:
                telegram.send_notice(call, notified)
        else:
            text = telegram.notice_text(call).replace("Tap below.", "Reply NO.")
            flow.spawn(_whatsapp(call, text, resolved["whatsapp"], notice=True))
    return names


def send_ping(call, contact, claim=None):
    from guard import flow
    resolved = targets(contact)
    names = enabled(contact, resolved=resolved)
    for name in names:
        flow.spawn(_deliver(name, call, contact, claim, resolved[name]))
    return names


def _page(content):
    return HTMLResponse('<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head>'
                        '<body style="font-family:system-ui;padding:24px;font-size:22px">' + content + '</body></html>',
                        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.get("/answer")
async def answer_page(d: str = ""):
    verified = telegram.verify_callback(d)
    if not verified:
        return _page("This link is no longer valid.")
    action, _ = verified
    label = "No, end the call" if action == "n" else "Yes, it's me"
    return _page(f'<form method="post" action="/answer"><input type="hidden" name="d" value="{html.escape(d)}">'
                 f'<button style="font-size:24px;padding:24px;width:100%">{html.escape(label)}</button></form>')


@router.post("/answer")
async def answer_post(request: Request):
    # HACK: this tiny form needs only URL-encoded data, avoiding a multipart dependency.
    d = parse_qs((await request.body()).decode("utf-8", errors="replace")).get("d", [""])[0]
    verified = telegram.verify_callback(d)
    if not verified:
        return _page("This link is no longer valid.")
    action, call_id = verified
    from guard import flow
    answer = "yes" if action == "y" else "no"
    won = await flow.on_tap(call_id, answer, channel="email")
    return _page(result_text(answer, won))
