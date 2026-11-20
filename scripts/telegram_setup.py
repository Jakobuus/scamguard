"""Register the webhook, discover chat ids, or run an offline signature check."""
import argparse
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from guard import settings, telegram


def selftest():
    with patch.dict(os.environ, {"APP_SECRET": "selftest-only"}):
        call_id = "c" * 36
        for action in ("y", "n"):
            data = telegram.callback_data(action, call_id)
            assert len(data.encode()) <= 64
            assert telegram.verify_callback(data) == (action, call_id)
            assert telegram.verify_callback(data[:-1] + ("0" if data[-1] != "0" else "1")) is None
            assert telegram.verify_callback(("n" if action == "y" else "y") + data[1:]) is None
    print("Callback selftest passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chat-id", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        selftest()
        return
    if not settings.REAL_TELEGRAM:
        print("Telegram is fake; set FAKE=0 and REAL_TELEGRAM=1 to use setup.")
        return
    try:
        if args.chat_id:
            info = telegram._api("getWebhookInfo", {})
            if info.get("url"):
                telegram._api("deleteWebhook", {})
            chats = set()
            for update in telegram._api("getUpdates", {}):
                message = update.get("message") or update.get("callback_query", {}).get("message", {})
                chat_id = message.get("chat", {}).get("id")
                if chat_id is not None:
                    chats.add(chat_id)
            for chat_id in sorted(chats):
                print(chat_id)
        else:
            base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
            secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
            if not base.startswith("https://") or not secret:
                raise ValueError("Webhook configuration missing")
            telegram._api("setWebhook", {
                "url": base + "/telegram/webhook", "secret_token": secret,
                "allowed_updates": ["callback_query", "message"],
            })
            info = json.dumps(telegram._api("getWebhookInfo", {}), indent=2)
            for value in (os.environ.get("TELEGRAM_BOT_TOKEN"), secret):
                if value:
                    info = info.replace(value, "[redacted]")
            print(info)
    except Exception:
        # Never include exception text: HTTP errors can contain the bot token.
        print("Telegram setup failed; check configuration and connectivity.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
