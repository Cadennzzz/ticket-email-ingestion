"""
Grouped, aggregated view of pending (source='email', promoted=0) scraped
transactions — designed for manual copy-paste into Working tickets copy.xlsm.

Groups by (normalized event, event_date, normalized ticket tier), further
split by section/row/seat conflicts and by meaningfully different prices
within the same tier (flagged rather than silently merged — see
grouping.py). This does NOT promote anything; it's purely a presentation
layer to make the pending data usable. You decide what to actually paste
into the workbook.

Run with:
    python pending_groups.py
"""

import sqlite3

from db import get_connection
from grouping import group_pending_rows, read_existing_event_names, suggest_event_name


def load_pending_rows():
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    try:
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM transactions WHERE source = 'email' AND promoted = 0"
            ).fetchall()
        ]
    finally:
        conn.close()
    return rows


def main() -> None:
    rows = load_pending_rows()
    if not rows:
        print("No pending scraped transactions.")
        return

    groups = group_pending_rows(rows)

    try:
        existing_names = read_existing_event_names()
    except FileNotFoundError:
        print("(Working tickets copy.xlsm not found — suggested names won't check for collisions.)\n")
        existing_names = set()

    print(f"--- {len(groups)} group(s) from {len(rows)} pending row(s) ---\n")

    for i, g in enumerate(groups, start=1):
        suggested_name = suggest_event_name(
            g["artist_or_event"], g["event_date"], g["tier"], existing_names
        )
        existing_names.add(suggested_name)  # so two groups this run don't collide with each other

        price_str = (
            f"${g['avg_price_per_ticket']:,.2f}" if g["avg_price_per_ticket"] is not None else "N/A"
        )

        print(f"Group {i}: {g['artist_or_event']!r} | {g['event_date']} | tier={g['tier']}")
        if g["flag"]:
            print(f"  FLAG: {g['flag']}")
        print(f"  Suggested Excel entry -> Event: {suggested_name!r}")
        print(
            f"    Quantity: {g['total_quantity']}   Total Cost: ${g['total_cost']:,.2f}   "
            f"Price/Ticket: {price_str}"
        )
        print(f"    Venue: {g['venue']}")
        print("  Contributing rows:")
        for r in g["rows"]:
            marker = "" if r.get("_counts_toward_total", True) else "  [duplicate order — excluded from totals]"
            print(
                f"    id={r.get('id')} uid={r.get('raw_email_uid')} qty={r.get('quantity')} "
                f"price/tix={r.get('price_per_ticket')} total={r.get('total_price')} "
                f"section={r.get('section')} row={r.get('row')} seat={r.get('seat')} "
                f"platform={r.get('platform')} purchase_date={r.get('purchase_date')}{marker}"
            )
        print()


if __name__ == "__main__":
    main()
