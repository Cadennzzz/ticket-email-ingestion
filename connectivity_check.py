"""
One-off IMAP connectivity check. Confirms auth works and prints the last
5 messages in the inbox. No LLM calls, no database — just proves the
connection works before anything else gets built.
"""

import os
import sys

from dotenv import load_dotenv
from imap_tools import MailBox, AND

load_dotenv()

GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")


def main() -> None:
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        print(
            "Missing GMAIL_USER or GMAIL_APP_PASSWORD in .env — "
            "check both are set with no quotes and no spaces."
        )
        sys.exit(1)

    try:
        with MailBox("imap.gmail.com").login(GMAIL_USER, GMAIL_APP_PASSWORD) as mailbox:
            messages = list(mailbox.fetch(AND(all=True), limit=5, reverse=True))

            if not messages:
                print("Connected successfully, but the inbox is empty.")
                return

            print(f"Connected as {GMAIL_USER}. Last {len(messages)} messages:\n")
            for msg in messages:
                print(f"UID:     {msg.uid}")
                print(f"From:    {msg.from_}")
                print(f"Subject: {msg.subject}")
                print("-" * 40)

    except Exception as e:
        print("IMAP connection/auth failed.")
        print(
            "Check: GMAIL_APP_PASSWORD is the 16-character app password "
            "(not your regular Gmail password), 2-Step Verification is on, "
            "and IMAP access is enabled on this account."
        )
        print(f"\nRaw error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
