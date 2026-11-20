import logging

from fastapi import APIRouter

from guard.contracts import Call, Contact

log = logging.getLogger("guard.telegram")
router = APIRouter()


def send_ping(call: Call, contact: Contact) -> None:
    # fake: log only. Real: sendMessage with Yes/No inline buttons to contact.telegram_chat_id
    log.info("ping %s: someone is calling as %s (call %s)", contact.id, contact.name, call.call_id)


def notify_owner(text: str) -> None:
    log.info("owner notified: %s", text)


@router.post("/telegram/webhook")
async def webhook():
    # TODO lane 1: parse the callback query, map to flow.on_tap(call_id, "yes"|"no")
    return {"ok": True}
