import asyncio
import hashlib
import hmac
import logging
import os
import re

import httpx
from fastapi import APIRouter, Request

from guard import settings
from guard.contracts import Call, Contact
from guard.brain import contacts

log = logging.getLogger("guard.telegram")
router = APIRouter()


def callback_data(action: str, call_id: str) -> str:
    secret = os.environ.get("APP_SECRET", "")
    if not secret or action not in ("y", "n") or not call_id or ":" in call_id:
        raise ValueError("Invalid callback configuration")
    payload = f"{action}:{call_id}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
    data = f"{payload}:{sig}"
    if len(data.encode()) > 64:
        raise ValueError("Callback too long")
    return data


def verify_callback(data: str) -> tuple[str, str] | None:
    try:
        action, call_id, _ = data.split(":")
        if hmac.compare_digest(data.encode(), callback_data(action, call_id).encode()):
            return action, call_id
    except (ValueError, TypeError, AttributeError):
        pass
    return None


def _api(method: str, payload: dict):
    if not settings.REAL_TELEGRAM:
        raise RuntimeError("Telegram is disabled")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise ValueError("Telegram token missing")
    response = httpx.post(f"https://api.telegram.org/bot{token}/{method}", json=payload, timeout=5)
    response.raise_for_status()
    body = response.json()
    if not body.get("ok"):
        raise RuntimeError("Telegram request failed")
    return body.get("result")


def asks_for(claim) -> str:
    """Shared request wording for the message and audience event."""
    if not claim or not (claim.amount or claim.method):
        return ""
    amount = (claim.amount or "money").strip()
    if re.fullmatch(r"\$?\s*\d[\d,]*(?:\.\d+)?", amount):
        raw = amount.replace("$", "").replace(",", "").strip()
        whole, dot, fraction = raw.partition(".")
        amount = "$" + format(int(whole), ",") + (dot + fraction if dot else "")
    method = (claim.method or "").strip().lower()
    if method in ("wire", "transfer", "bank transfer"):
        return amount + " by wire"
    if method:
        return amount + " in " + method
    return amount


def ping_text(contact: Contact, claim=None) -> str:
    text = f"Someone is calling {settings.OWNER_NAME} as you"
    request = asks_for(claim)
    if request:
        text += ", asking for " + request
    attempts = contacts.attempts_today(contact.id)
    if attempts:
        text += f" (attempt {attempts + 1} today)"
    return text + ". Is this you?"


def send_ping(call: Call, contact: Contact, claim=None) -> None:
    text = ping_text(contact, claim)
    if not settings.REAL_TELEGRAM:
        log.info("ping %s: %s (call %s)", contact.id, text, call.call_id)
        return
    try:
        chat_id = contact.telegram_chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        if not chat_id:
            raise ValueError("Telegram chat missing")
        # HACK: bounded synchronous request keeps the existing synchronous seam.
        _api("sendMessage", {
            "chat_id": chat_id,
            "text": text,
            "reply_markup": {"inline_keyboard": [[
                {"text": "Yes, it's me", "callback_data": callback_data("y", call.call_id)},
                {"text": "No", "callback_data": callback_data("n", call.call_id)},
            ]]},
        })
    except Exception:
        log.warning("Telegram ping failed; using fake behaviour")


def send_notice(call: Call, contact: Contact) -> None:
    text = (f"Someone reached {settings.OWNER_NAME} with your code word. Not you? Tap below."
            if call.decided_by == "code_word" else
            f"You are calling {settings.OWNER_NAME} right now. Not you? Tap below.")
    if not settings.REAL_TELEGRAM:
        log.info("notice %s: %s (call %s)", contact.id, text, call.call_id)
        return
    try:
        chat_id = contact.telegram_chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        if not chat_id:
            raise ValueError("Telegram chat missing")
        _api("sendMessage", {
            "chat_id": chat_id, "text": text,
            "reply_markup": {"inline_keyboard": [[
                {"text": "Not me", "callback_data": callback_data("n", call.call_id)},
            ]]},
        })
    except Exception:
        log.warning("Telegram notice failed; using fake behaviour")


def notify_owner(text: str) -> None:
    if not settings.REAL_TELEGRAM:
        log.info("owner notified: %s", text)
        return
    try:
        chat_id = os.environ.get("OWNER_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID")
        if not chat_id:
            raise ValueError("Telegram chat missing")
        _api("sendMessage", {"chat_id": chat_id, "text": text})
    except Exception:
        log.warning("Telegram owner notification failed; using fake behaviour")


@router.post("/telegram/webhook")
async def webhook(request: Request = None):
    if not settings.REAL_TELEGRAM or request is None:
        return {"ok": True}
    try:
        secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
        supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if secret and not hmac.compare_digest(secret.encode(), supplied.encode()):
            return {"ok": True}
        update = await request.json()
        query = update.get("callback_query")
        if not isinstance(query, dict):
            return {"ok": True}
        verified = verify_callback(query.get("data", ""))
        if verified is None:
            return {"ok": True}
        action, call_id = verified
        from guard import flow

        won = await flow.on_tap(call_id, "yes" if action == "y" else "no")
        text = ("Thanks. Putting the call through." if action == "y" else "Thanks. Call ended.") if won else "Too late, already decided."
        # Attempt both operations even if answering the callback fails.
        failed = False
        try:
            await asyncio.to_thread(_api, "answerCallbackQuery", {"callback_query_id": query["id"]})
        except Exception:
            failed = True
        try:
            message = query["message"]
            await asyncio.to_thread(_api, "editMessageText", {
                "chat_id": message["chat"]["id"], "message_id": message["message_id"],
                "text": text, "reply_markup": {"inline_keyboard": []},
            })
        except Exception:
            failed = True
        if failed:
            log.warning("Telegram callback response failed; using fake behaviour")
    except Exception:
        # Never log exception text: HTTP errors can contain the bot token.
        log.warning("Telegram webhook failed; using fake behaviour")
    return {"ok": True}
