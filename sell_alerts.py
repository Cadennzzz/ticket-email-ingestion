"""
Morning sell alert: unsold inventory for events happening today or tomorrow.

Reuses match.py's FIFO lot-matching, so "unsold" means exactly what the
match summary calls unsold leftover — including its excel-only scope
(source='excel'); sales that exist only as scraped emails don't count until
promoted. Lots are summed per (normalized event, event_date), one line each.

Sends the result as a push via ntfy.sh to the topic in the NTFY_TOPIC env
var (never hardcoded: the repo may be public, and anyone who knows a topic
can read it). Nothing is sent when there's nothing to report.

Run with:
    python sell_alerts.py                 # send for today/tomorrow (Mountain)
    python sell_alerts.py --dry-run       # print instead of sending
    python sell_alerts.py --date 2026-10-16 --dry-run
"""

import argparse
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from db import get_connection
from match import load_rows, match_transactions, normalize_event

LOCAL_TZ = ZoneInfo("America/Denver")
NTFY_URL = "https://ntfy.sh/{topic}"


def find_unsold(rows, dates):
    """
    Return [(event_name, unsold_qty_or_None, event_date)] for unsold buy
    leftovers whose event_date is in `dates`. Quantity is None when any lot
    in the group has no recorded quantity (can't be summed honestly).
    """
    _, unmatched_buys, _ = match_transactions(rows)

    groups = defaultdict(lambda: {"name": None, "qty": 0, "unknown": False})
    for row, leftover in unmatched_buys:
        if row["event_date"] not in dates:
            continue
        g = groups[(normalize_event(row["artist_or_event"]), row["event_date"])]
        g["name"] = g["name"] or row["artist_or_event"]
        if leftover is None:
            g["unknown"] = True
        else:
            g["qty"] += leftover

    results = [
        (g["name"], None if g["unknown"] else g["qty"], event_date)
        for (_, event_date), g in groups.items()
    ]
    return sorted(results, key=lambda r: (r[2], (r[0] or "").lower()))


def format_lines(results) -> str:
    lines = []
    for name, qty, event_date in results:
        qty_text = "?" if qty is None else str(qty)
        lines.append(f"{name} — {qty_text} unsold — {event_date}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--dry-run", action="store_true", help="print instead of sending")
    parser.add_argument("--date", help="treat this YYYY-MM-DD as today (default: today in Mountain time)")
    args = parser.parse_args()

    today = date.fromisoformat(args.date) if args.date else datetime.now(LOCAL_TZ).date()
    dates = {today.isoformat(), (today + timedelta(days=1)).isoformat()}

    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not args.dry_run and not topic:
        sys.exit("NTFY_TOPIC is not set (use --dry-run to print instead).")

    conn = get_connection()
    try:
        rows = load_rows(conn)
    finally:
        conn.close()

    results = find_unsold(rows, dates)
    if not results:
        print(f"No unsold inventory for {min(dates)} or {max(dates)}; nothing to send.")
        return

    body = format_lines(results)
    if args.dry_run:
        print(body)
        return

    resp = requests.post(
        NTFY_URL.format(topic=topic),
        data=body.encode("utf-8"),
        headers={"Title": "Unsold tickets: today/tomorrow", "Tags": "ticket"},
        timeout=30,
    )
    resp.raise_for_status()
    print(f"Sent alert for {len(results)} event(s).")


if __name__ == "__main__":
    main()
