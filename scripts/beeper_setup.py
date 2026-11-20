"""List accessible chats, or send one operator-requested test message."""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from guard.channels import beeper_request, message_path


CONTACTS_PATH = Path(__file__).resolve().parent.parent / "data" / "contacts.json"


def set_contact(contact_id, chat_id):
    if not chat_id.startswith("!"):
        raise ValueError("Expected a Beeper chat id")
    local = CONTACTS_PATH.with_name("contacts.local.json")
    builtin = json.loads(CONTACTS_PATH.read_text())
    entries = json.loads(local.read_text()) if local.exists() else []
    record = next((dict(c) for c in builtin if c["id"] == contact_id), {})
    for entry in entries:
        if entry["id"] == contact_id:
            record.update(entry)
    if not record:
        raise ValueError("Unknown contact")
    record["whatsapp_chat_id"] = chat_id
    entries = [c for c in entries if c["id"] != contact_id] + [record]
    # HACK: the operator runs setup alone, so this overlay update needs no writer lock.
    temporary = local.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(entries, indent=2) + "\n")
    temporary.replace(local)
    print(contact_id, len(chat_id))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--send-test", metavar="CHAT_ID")
    mode.add_argument("--find", metavar="NAME")
    mode.add_argument("--set-contact", nargs=2, metavar=("CONTACT_ID", "CHAT_ID"))
    args = parser.parse_args()
    if args.set_contact:
        try:
            set_contact(*args.set_contact)
        except (OSError, ValueError, TypeError, KeyError):
            parser.exit(1, "Contact update failed. Check the contact id, chat id and contacts files.\n")
        return
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
                if not args.find or args.find.casefold() in str(chat.get("title", "")).casefold():
                    print(f"{chat.get('title', '')}\t{chat.get('network', '')}\t{chat.get('type', '')}\t{chat['id']}")
            if not page.get("hasMore"):
                break
            params = {"limit": 200, "cursor": page["oldestCursor"], "direction": "before"}
    except Exception:
        parser.exit(1, "Beeper request failed. Check Desktop API settings and token.\n")


if __name__ == "__main__":
    main()
