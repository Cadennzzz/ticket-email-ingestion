"""
List scraped-but-unpromoted transactions.

Read-only view of everything ingest.py pulled from email (source='email')
that hasn't been promoted into the canonical (source='excel') dataset yet
— i.e. what's excluded from match.py, export.py, and the dashboard's real
metrics until you decide how promotion should work.

Run with:
    python review_pending.py
"""

import pandas as pd

from db import get_connection


def main() -> None:
    conn = get_connection()
    try:
        df = pd.read_sql_query(
            """
            SELECT artist_or_event AS event, platform, transaction_type AS type,
                   price_per_ticket, total_price, quantity, purchase_date,
                   event_date, needs_review, review_reason, raw_email_uid
            FROM transactions
            WHERE source = 'email' AND promoted = 0
            ORDER BY purchase_date
            """,
            conn,
        )
    finally:
        conn.close()

    if df.empty:
        print("No pending scraped transactions awaiting review.")
        return

    print(f"--- {len(df)} pending transaction(s) awaiting review/promotion ---\n")
    with pd.option_context("display.max_rows", None, "display.width", 200):
        print(df.to_string(index=False))


if __name__ == "__main__":
    main()
