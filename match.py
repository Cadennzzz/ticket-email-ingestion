"""
Buy/sell profit matching.

Groups 'buy' and 'sell' transactions by normalized artist_or_event +
event_date, then does FIFO lot-matching by quantity within each group:
a bulk buy can be consumed across several smaller sells (or vice versa),
oldest-first by purchase_date. This handles split fills, e.g. a single
buy of 6 tickets sold off as separate listings of 2+2+1+1 — a very common
pattern in this business's real data, where treating every buy/sell as a
single indivisible lot left most rows unmatched.

Only whole matched quantity is ever recorded — leftover quantity that
couldn't be paired (more bought than sold so far, or vice versa) is left
unmatched and printed, never guessed at. A row with no quantity at all
can't participate in quantity-based matching and is always unmatched.

Results are stored in a `matches` table in transactions.db. Because a buy
or sell can now be split across multiple matches, buy_id/sell_id are no
longer unique per row — each row represents one FIFO-consumed slice.

Run with:
    python match.py
"""

import re
from collections import defaultdict
from typing import Optional

from db import get_connection

_TRAILING_SUFFIXES = [
    " tickets",
    " ticket",
    " presale",
    " general admission",
    " ga",
]


def normalize_event(name: str) -> str:
    """Lowercase, trim, and strip common trailing noise for grouping purposes."""
    if not name:
        return ""
    name = name.strip().lower()
    name = re.sub(r"\(.*?\)\s*$", "", name).strip()

    changed = True
    while changed:
        changed = False
        for suffix in _TRAILING_SUFFIXES:
            if name.endswith(suffix):
                name = name[: -len(suffix)].strip()
                changed = True

    name = re.sub(r"\s+", " ", name)
    return name.rstrip("-–—:,. ").strip()


def load_rows(conn):
    # Only the canonical (Excel-sourced) dataset participates in matching.
    # Scraped emails (source='excel' vs 'email') stay isolated until
    # explicitly promoted — see review_pending.py.
    cur = conn.execute(
        """
        SELECT id, transaction_type, artist_or_event, venue, event_date,
               quantity, price_per_ticket, total_price, purchase_date
        FROM transactions
        WHERE transaction_type IN ('buy', 'sell') AND source = 'excel'
        """
    )
    columns = [d[0] for d in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def build_matches_table(conn) -> None:
    conn.execute("DROP TABLE IF EXISTS matches")
    conn.execute(
        """
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            buy_id INTEGER NOT NULL,
            sell_id INTEGER NOT NULL,
            event TEXT,
            venue TEXT,
            event_date TEXT,
            quantity INTEGER,
            purchase_cost REAL,
            gross_sale REAL,
            net_profit REAL,
            roi REAL
        )
        """
    )
    conn.commit()


def per_ticket_price(row) -> Optional[float]:
    """price_per_ticket if given, else total_price / quantity."""
    price = row["price_per_ticket"]
    if price is not None:
        return price
    total = row["total_price"]
    qty = row["quantity"]
    if total is not None and qty:
        return total / qty
    return None


def match_transactions(rows):
    """
    FIFO lot-match buys to sells by quantity within each (event, date) group.

    Returns:
        matched_slices: list of (buy_row, sell_row, matched_quantity)
        unmatched_buys: list of (buy_row, leftover_quantity_or_None)
        unmatched_sells: list of (sell_row, leftover_quantity_or_None)
    """
    groups = defaultdict(lambda: {"buys": [], "sells": []})
    for row in rows:
        key = (normalize_event(row["artist_or_event"]), row["event_date"])
        bucket = "buys" if row["transaction_type"] == "buy" else "sells"
        groups[key][bucket].append(row)

    matched_slices = []
    unmatched_buys = []
    unmatched_sells = []

    for bucket in groups.values():
        buys = sorted(bucket["buys"], key=lambda r: (r["purchase_date"] or "", r["id"]))
        sells = sorted(bucket["sells"], key=lambda r: (r["purchase_date"] or "", r["id"]))

        # Rows with no quantity at all can't be lot-matched — flag outright.
        buy_lots = [[b, b["quantity"]] for b in buys if b["quantity"]]
        sell_lots = [[s, s["quantity"]] for s in sells if s["quantity"]]
        for b in buys:
            if not b["quantity"]:
                unmatched_buys.append((b, None))
        for s in sells:
            if not s["quantity"]:
                unmatched_sells.append((s, None))

        bi, si = 0, 0
        while bi < len(buy_lots) and si < len(sell_lots):
            buy_row, buy_remaining = buy_lots[bi]
            sell_row, sell_remaining = sell_lots[si]
            matched_qty = min(buy_remaining, sell_remaining)

            matched_slices.append((buy_row, sell_row, matched_qty))

            buy_lots[bi][1] -= matched_qty
            sell_lots[si][1] -= matched_qty
            if buy_lots[bi][1] == 0:
                bi += 1
            if sell_lots[si][1] == 0:
                si += 1

        for row, remaining in buy_lots[bi:]:
            if remaining > 0:
                unmatched_buys.append((row, remaining))
        for row, remaining in sell_lots[si:]:
            if remaining > 0:
                unmatched_sells.append((row, remaining))

    return matched_slices, unmatched_buys, unmatched_sells


def build_insert_rows(matched_slices):
    insert_rows = []
    total_net_profit = 0.0
    for buy, sell, qty in matched_slices:
        buy_unit = per_ticket_price(buy)
        sell_unit = per_ticket_price(sell)

        purchase_cost = buy_unit * qty if buy_unit is not None else None
        gross_sale = sell_unit * qty if sell_unit is not None else None

        if purchase_cost is None or gross_sale is None:
            net_profit = None
            roi = None
        else:
            net_profit = gross_sale - purchase_cost
            roi = (net_profit / purchase_cost) if purchase_cost else None
            total_net_profit += net_profit

        insert_rows.append(
            (
                buy["id"],
                sell["id"],
                buy["artist_or_event"] or sell["artist_or_event"],
                buy["venue"] or sell["venue"],
                buy["event_date"] or sell["event_date"],
                qty,
                purchase_cost,
                gross_sale,
                net_profit,
                roi,
            )
        )
    return insert_rows, total_net_profit


def format_leftover(row, leftover) -> str:
    total_qty = row["quantity"]
    if leftover is None:
        return str(total_qty)
    if total_qty and leftover != total_qty:
        return f"{leftover} of {total_qty}"
    return str(leftover)


def main() -> None:
    conn = get_connection()
    try:
        rows = load_rows(conn)
        matched_slices, unmatched_buys, unmatched_sells = match_transactions(rows)
        insert_rows, total_net_profit = build_insert_rows(matched_slices)

        build_matches_table(conn)
        conn.executemany(
            """
            INSERT INTO matches
                (buy_id, sell_id, event, venue, event_date, quantity,
                 purchase_cost, gross_sale, net_profit, roi)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            insert_rows,
        )
        conn.commit()
    finally:
        conn.close()

    print("--- Match summary ---")
    print(f"Matched slices (buy/sell pairs, incl. splits): {len(matched_slices)}")
    print(f"Buy rows with unsold leftover:                 {len(unmatched_buys)}")
    print(f"Sell rows with unmatched leftover:              {len(unmatched_sells)}")
    print(f"Total net profit (matched slices):              ${total_net_profit:,.2f}")

    if unmatched_buys:
        print("\nUnmatched/partially unmatched buy rows:")
        for r, leftover in unmatched_buys:
            print(
                f"  id={r['id']} event={r['artist_or_event']!r} "
                f"date={r['event_date']} qty={format_leftover(r, leftover)}"
            )

    if unmatched_sells:
        print("\nUnmatched/partially unmatched sell rows:")
        for r, leftover in unmatched_sells:
            print(
                f"  id={r['id']} event={r['artist_or_event']!r} "
                f"date={r['event_date']} qty={format_leftover(r, leftover)}"
            )


if __name__ == "__main__":
    main()
