"""
One-off backfill of sender / sender_name / recipient / received_date for
email rows saved before those columns existed.

Read-only against IMAP: fetches headers only, by the stored raw_email_uid,
without marking anything seen. Only fills columns that are still NULL, so
it's safe to re-run. Excel-imported rows are left alone.

Dry run by default — prints what it would write. Pass --apply to update.

Usage:
    python backfill_email_headers.py [--apply]
"""

import argparse
import sys

from imap_tools import AND, MailBox

from db import get_connection
from ingest import GMAIL_APP_PASSWORD, GMAIL_USER, email_header_fields

FIELDS = ["sender", "sender_name", "recipient", "received_date"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill email header columns on existing rows.")
    parser.add_argument("--apply", action="store_true", help="Write updates (default is a dry run)")
    args = parser.parse_args()

    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        print("Missing GMAIL_USER or GMAIL_APP_PASSWORD in .env.")
        sys.exit(1)

    conn = get_connection()
    try:
        null_check = " OR ".join(f"{f} IS NULL" for f in FIELDS)
        rows = conn.execute(
            f"SELECT id, raw_email_uid, {', '.join(FIELDS)} FROM transactions "
            f"WHERE source = 'email' AND ({null_check})"
        ).fetchall()
        if not rows:
            print("Nothing to backfill.")
            return

        by_uid = {row[1]: row for row in rows}
        print(f"{len(by_uid)} email rows missing header fields. Fetching headers...\n")

        found = {}
        with MailBox("imap.gmail.com").login(GMAIL_USER, GMAIL_APP_PASSWORD) as mailbox:
            for msg in mailbox.fetch(AND(uid=list(by_uid)), mark_seen=False, headers_only=True, bulk=True):
                found[msg.uid] = email_header_fields(msg)

        updated = 0
        for uid, row in by_uid.items():
            fields = found.get(uid)
            if fields is None:
                print(f"  id={row[0]} uid={uid}: not found in mailbox, skipped")
                continue
            current = dict(zip(FIELDS, row[2:]))
            changes = {f: fields[f] for f in FIELDS if current[f] is None and fields[f] is not None}
            if not changes:
                continue
            print(f"  id={row[0]} uid={uid}: {changes}")
            if args.apply:
                assignments = ", ".join(f"{f} = ?" for f in changes)
                conn.execute(
                    f"UPDATE transactions SET {assignments} WHERE id = ?",
                    [*changes.values(), row[0]],
                )
            updated += 1

        if args.apply:
            conn.commit()
        print(f"\n{'Updated' if args.apply else 'Would update'} {updated} rows; "
              f"{len(by_uid) - len(found)} UIDs not found.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
