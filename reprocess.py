"""
Re-run specific inbox emails through the ingestion pipeline.

Dry run by default: works on a temporary copy of transactions.db and
prints what would be saved or skipped for each uid. With --apply it writes
to the real database (back it up first).

Usage:
    python reprocess.py 64 65 66              # dry run
    python reprocess.py 64 65 66 --replace    # dry run, re-extracting uids already saved
    python reprocess.py 64 65 66 --replace --apply

--replace deletes the existing unpromoted email rows for the given uids
before re-extracting them; without it, already-saved uids are left alone.
Emails are processed in ascending uid order (as ingest.py does), paced to
stay under Gemini's free-tier limit of 15 requests/minute.
"""

import argparse
import shutil
import sqlite3
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

from google import genai
from imap_tools import AND, MailBox

import db
import ingest

# One email is up to 2 LLM calls (extraction + link fallback).
SECONDS_PER_EMAIL = 9

SUMMARY_FIELDS = (
    "email_kind", "transaction_type", "platform", "order_id", "artist_or_event",
    "event_date", "quantity", "price_per_ticket", "total_price", "payout_amount", "needs_review",
)


def run(uids: list, db_path: Path, replace: bool = False) -> list:
    """Process `uids` against the database at `db_path`. Returns one dict
    per uid: uid, subject, outcome, row (the saved fields or None), reason."""
    db.DB_PATH = db_path
    uids = sorted({str(u) for u in uids}, key=int)

    if replace:
        conn = sqlite3.connect(db_path)
        marks = ",".join("?" for _ in uids)
        conn.execute(
            f"DELETE FROM transactions WHERE source = 'email' AND promoted = 0 AND raw_email_uid IN ({marks})",
            uids,
        )
        conn.commit()
        conn.close()

    client = genai.Client(api_key=ingest.GEMINI_API_KEY)
    with MailBox("imap.gmail.com").login(ingest.GMAIL_USER, ingest.GMAIL_APP_PASSWORD) as mailbox:
        messages = {m.uid: m for m in mailbox.fetch(AND(uid=uids), mark_seen=False)}

    results = []
    for uid in uids:
        msg = messages.get(uid)
        if msg is None:
            results.append({"uid": uid, "subject": None, "outcome": "not in mailbox", "row": None, "reason": None})
            continue
        if db.is_processed(uid):
            results.append({"uid": uid, "subject": msg.subject, "outcome": "already saved", "row": None, "reason": None})
            continue

        started = time.time()
        stats = defaultdict(int)
        data = ingest.process_message(client, msg, stats)
        if data is None:
            outcome = next((k for k in stats if k.startswith("skipped_")), "not a transaction")
            results.append({"uid": uid, "subject": msg.subject, "outcome": outcome, "row": None, "reason": None})
        else:
            ingest.save_result(data)
            outcome = "saved" if data.get("replaces_id") is None else f"saved, replaced id={data['replaces_id']}"
            results.append({
                "uid": uid, "subject": msg.subject, "outcome": outcome,
                "row": {k: data.get(k) for k in SUMMARY_FIELDS}, "reason": data.get("review_reason"),
            })
        time.sleep(max(0.0, SECONDS_PER_EMAIL - (time.time() - started)))

    # Same end-of-run pass as ingest.py; mark any row it removed.
    stats = defaultdict(int)
    before = {r["raw_email_uid"] for r in db.unmatched_transfer_rows()}
    ingest.drop_matched_transfers(stats)
    dropped = before - {r["raw_email_uid"] for r in db.unmatched_transfer_rows()}
    for r in results:
        if r["uid"] in dropped:
            r["outcome"] = "saved, then dropped (transfer matched later)"
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-run specific inbox emails through ingestion.")
    parser.add_argument("uids", nargs="+", help="IMAP uids to process")
    parser.add_argument("--replace", action="store_true", help="delete existing unpromoted email rows for these uids first")
    parser.add_argument("--apply", action="store_true", help="write to the real transactions.db instead of a temp copy")
    args = parser.parse_args()

    real_db = db.DB_PATH
    if args.apply:
        target = real_db
    else:
        target = Path(tempfile.mkdtemp()) / "transactions.db"
        shutil.copy(real_db, target)
    print(f"{'APPLYING to' if args.apply else 'Dry run on a copy:'} {target}\n")

    for r in run(args.uids, target, replace=args.replace):
        print(f"uid {r['uid']:>4} | {(r['subject'] or '')[:55]:<55} | {r['outcome']}")
        if r["row"]:
            print(f"       {r['row']}")
        if r["reason"]:
            print(f"       review_reason: {r['reason']}")


if __name__ == "__main__":
    sys.exit(main())
