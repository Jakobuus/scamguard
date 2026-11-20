"""Non-blocking question delivery; all answers share the flow's atomic decision."""
import asyncio
import html
import logging
import os
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, quote, urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from guard import settings, state, telegram

log = logging.getLogger("guard.channels")
router = APIRouter()


def enabled() -> list[str]:
    # HACK: Telegram keeps its existing fake ping even without bot configuration.
    names = ["telegram"]
    for name, flag, required in (
        ("whatsapp", "REAL_BEEPER", ("BEEPER_ACCESS_TOKEN", "BEEPER_CHAT_ID")),
        ("email", "REAL_EMAIL", ("RESEND_API_KEY", "PING_EMAIL_TO", "PUBLIC_BASE_URL", "APP_SECRET")),
    ):
        if settings._flag(flag) and all(os.environ.get(key) for key in required):
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


OWN_PREFIXES = ("someone is calling", "thanks.", "too late", "call guard test")


def _norm(text):
    return " ".join((text or "").replace("<br>", " ").replace("<br/>", " ").split()).casefold()


def _own_line(text):
    return _norm(text).startswith(OWN_PREFIXES)


def _why(e):
    # Never log URLs, headers or HTTP bodies: they can carry credentials.
    if isinstance(e, httpx.HTTPStatusError):
        return f"{type(e).__name__} status {e.response.status_code}"
    if isinstance(e, httpx.HTTPError):
        return type(e).__name__
    return f"{type(e).__name__}: {e}"


async def _whatsapp(call, text):
    if not settings.REAL_BEEPER:
        log.info("fake whatsapp ping (call %s): %s", call.call_id, text)
        return
    try:
        chat_id = os.environ["BEEPER_CHAT_ID"]
        path = message_path(chat_id)
        # One line: the listed text of a multi-line message comes back with <br>.
        question = text + " Reply NO or YES."
        _latest[chat_id] = call.call_id
        before = await asyncio.to_thread(beeper_request, "GET", path)
        seen = {m.get("id") for m in before.get("items", [])}
        try:
            # Sending can take longer than a poll; a timeout does not mean it was not sent.
            await asyncio.to_thread(beeper_request, "POST", path, timeout=15, json={"text": question})
        except httpx.TimeoutException:
            log.warning("WhatsApp send timed out; polling for the reply anyway")
        deadline = time.monotonic() + 90
        # HACK: poll only the latest page; the demo chat has low message volume.
        while time.monotonic() < deadline:
            if _latest.get(chat_id) != call.call_id:
                return
            current = state.get_call(call.call_id)
            if not current or current.state not in ("pending", "unverified"):
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
                    if (_own_line(body) or message.get("isDeleted")
                            or message.get("type", "TEXT") != "TEXT"):
                        continue
                    answer = reply_answer(body)
                    if answer:
                        if _latest.get(chat_id) != call.call_id:
                            return
                        from guard import flow
                        won = await flow.on_tap(call.call_id, answer, channel="whatsapp")
                        await asyncio.to_thread(beeper_request, "POST", path, timeout=15,
                                                json={"text": result_text(answer, won)})
                        return
                except Exception:
                    log.warning("Skipped one bad WhatsApp message")
    except Exception as e:
        log.warning("WhatsApp question failed: %s", _why(e))


def _email(call, text):
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
                                "to": [os.environ["PING_EMAIL_TO"]], "subject": text,
                                "html": f"<p>{html.escape(text)}</p>" + "".join(links)})
    response.raise_for_status()


async def _deliver(name, call, contact, claim):
    try:
        if name == "telegram":
            await asyncio.to_thread(telegram.send_ping, call, contact, claim)
        elif name == "whatsapp":
            await _whatsapp(call, telegram.ping_text(contact, claim))
        else:
            await asyncio.to_thread(_email, call, telegram.ping_text(contact, claim))
    except Exception as e:
        log.warning("Question delivery failed on %s: %s", name, _why(e))


def send_ping(call, contact, claim=None):
    from guard import flow
    names = enabled()
    for name in names:
        flow.spawn(_deliver(name, call, contact, claim))
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
