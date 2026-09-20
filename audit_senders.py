"""
Read-only sender-domain audit.

Scans a mailbox, extracts sender domains, counts occurrences, and checks
each domain against the allowlist in allowlist.py. Domains that show up
often but aren't allowlisted are flagged as likely-relevant-but-missing,
so you can spot ticket platforms that should be added.

Defaults to the destination inbox configured in .env (GMAIL_USER /
GMAIL_APP_PASSWORD). To audit a different mailbox (e.g. one of the two
source accounts), pass --mailbox-user/--mailbox-password to override.

Never marks messages as read or otherwise modifies any mailbox.

Usage:
    python audit_senders.py --label destination
    python audit_senders.py --mailbox-user source1@gmail.com --mailbox-password APP_PASSWORD --label source1
    python audit_senders.py --mailbox-user source2@gmail.com --mailbox-password APP_PASSWORD --label source2 --limit 500
"""

import argparse
import os
import sys
from collections import Counter

from dotenv import load_dotenv
from imap_tools import AND, MailBox

from allowlist import is_allowlisted

load_dotenv()

DEFAULT_GMAIL_USER = os.getenv("GMAIL_USER")
DEFAULT_GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

# Domains that see meaningful volume but aren't allowlisted are worth a
# human look; anything rarer is more likely newsletters/receipts/etc.
FLAG_THRESHOLD = 3


def sender_domain(from_address: str) -> str:
    if not from_address or "@" not in from_address:
        return "(unknown)"
    return from_address.rsplit("@", 1)[-1].strip().lower().rstrip(".")


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only audit of sender domains in a mailbox.")
    parser.add_argument("--limit", type=int, default=None, help="Max number of messages to fetch (default: all)")
    parser.add_argument("--mailbox-user", default=None, help="Override mailbox login (default: GMAIL_USER from .env)")
    parser.add_argument("--mailbox-password", default=None, help="Override mailbox app password (default: GMAIL_APP_PASSWORD from .env)")
    parser.add_argument("--label", default=None, help="Label for this run, shown in the output header (e.g. destination, source1, source2)")
    args = parser.parse_args()

    mailbox_user = args.mailbox_user or DEFAULT_GMAIL_USER
    mailbox_password = args.mailbox_password or DEFAULT_GMAIL_APP_PASSWORD

    if not mailbox_user or not mailbox_password:
        print(
            "Missing mailbox credentials. Either set GMAIL_USER/GMAIL_APP_PASSWORD "
            "in .env, or pass --mailbox-user/--mailbox-password explicitly."
        )
        sys.exit(1)

    label = args.label or mailbox_user
    print(f"--- Sender audit: {label} ({mailbox_user}) ---\n")

    domain_counts = Counter()

    # mark_seen=False and headers_only=True: never touches read/unread
    # state or any other mailbox state, and never fetches bodies/attachments.
    with MailBox("imap.gmail.com").login(mailbox_user, mailbox_password) as mailbox:
        messages = mailbox.fetch(AND(all=True), limit=args.limit, reverse=True, mark_seen=False, headers_only=True)

        scanned = 0
        for msg in messages:
            scanned += 1
            domain_counts[sender_domain(msg.from_)] += 1

    if scanned == 0:
        print("No messages found.")
        return

    allowlisted_total = 0
    flagged = []

    print(f"{'Domain':<40} {'Count':>6}  Allowlisted")
    print("-" * 65)
    for domain, count in domain_counts.most_common():
        allowed = is_allowlisted(f"x@{domain}")
        if allowed:
            allowlisted_total += count
        elif count >= FLAG_THRESHOLD:
            flagged.append((domain, count))
        print(f"{domain:<40} {count:>6}  {'yes' if allowed else 'no'}")

    print(f"\nTotal messages scanned: {scanned}")
    print(f"Allowlisted:            {allowlisted_total}")
    print(f"Unique sender domains:  {len(domain_counts)}")

    print(f"\n--- Likely-relevant but not allowlisted (>= {FLAG_THRESHOLD} messages) ---")
    if flagged:
        for domain, count in flagged:
            print(f"  {domain} ({count} messages)")
    else:
        print("  None.")


if __name__ == "__main__":
    main()
