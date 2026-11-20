"""List accessible chats, or send one operator-requested test message."""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from guard.channels import beeper_request, message_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send-test", metavar="CHAT_ID")
    args = parser.parse_args()
    if not os.environ.get("BEEPER_ACCESS_TOKEN"):
        parser.exit(1, "Set BEEPER_ACCESS_TOKEN first.\n")
    try:
        if args.send_test:
            beeper_request("POST", message_path(args.send_test), json={"text": "Call Guard test. No action needed."})
            print("Test sent.")
            return
        params = {"limit": 200}
        while True:
            page = beeper_request("GET", "/v1/chats", params=params)
            for chat in page.get("items", []):
                print(f"{chat.get('title', '')}\t{chat.get('network', '')}\t{chat['id']}")
            if not page.get("hasMore"):
                break
            params = {"limit": 200, "cursor": page["oldestCursor"], "direction": "before"}
    except Exception:
        parser.exit(1, "Beeper request failed. Check Desktop API settings and token.\n")


if __name__ == "__main__":
    main()
