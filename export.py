"""
Excel export for ticket bookkeeping.

Reads transactions.db (transactions + matches tables, the latter produced
by match.py) and writes/replaces the BL, SL, and Pending sheets in
EmailScrape.xlsx. BL/SL cover the canonical (source='excel') dataset;
Pending is a purely informational list of scraped-but-unpromoted
(source='email') rows that don't already have a matching line in the sheet
(see crosscheck.find_excel_matches), and never feeds Net Profit/ROI. Any other sheet in
that workbook, and Working tickets copy.xlsm, are left untouched.

Run with:
    python export.py
"""

import sqlite3
from pathlib import Path

import pandas as pd
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font

from crosscheck import find_excel_matches
from manual_marks import load_marks
from db import get_connection

EXPORT_PATH = Path(__file__).parent / "EmailScrape.xlsx"

DATE_FORMAT = "yyyy-mm-dd"
CURRENCY_FORMAT = "$#,##0.00"
PERCENT_FORMAT = "0.00%"

BL_COLUMNS = [
    "Status",
    "Event",
    "Event Date",
    "Date Purchased",
    "Days to Sell",
    "Venue",
    "$ per Tix",
    "Quantity",
    "Total Cost",
    "Date Sold",
    "Paid out?",
    "Payout Date",
    "Account",
    "Lysted?",
]
BL_DATE_COLS = {"Event Date", "Date Purchased", "Date Sold", "Payout Date"}
BL_CURRENCY_COLS = {"$ per Tix", "Total Cost"}
BL_PERCENT_COLS = set()

SL_COLUMNS = [
    "SID",
    "Event",
    "Venue",
    "Event Date",
    "Date Sold",
    "Days Until Event",
    "Tickets Sold",
    "Purchase Price (per ticket)",
    "Sell Price (per ticket)",
    "Marketplace",
    "Transferred?",
    "Total Sale After Fees",
    "Gross Sale",
    "Net Profit",
    "ROI",
]
SL_DATE_COLS = {"Event Date", "Date Sold"}
SL_CURRENCY_COLS = {
    "Purchase Price (per ticket)",
    "Sell Price (per ticket)",
    "Total Sale After Fees",
    "Gross Sale",
    "Net Profit",
}
SL_PERCENT_COLS = {"ROI"}

PENDING_COLUMNS = [
    "Type",
    "Event",
    "Venue",
    "Event Date",
    "Purchase Date",
    "Quantity",
    "Price per Ticket",
    "Total Price",
    "Platform",
    "Needs Review",
    "Review Reason",
]
PENDING_DATE_COLS = {"Event Date", "Purchase Date"}
PENDING_CURRENCY_COLS = {"Price per Ticket", "Total Price"}
PENDING_PERCENT_COLS = set()


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (name,)
    )
    return cur.fetchone() is not None


def parse_date(value):
    """Parse a date/timestamp string into a naive datetime for Excel, or None."""
    if value is None or value == "":
        return None
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.to_pydatetime().replace(tzinfo=None)


def load_data():
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    try:
        # Only the canonical (Excel-sourced) dataset is exported. Scraped
        # emails (source='email') stay isolated until explicitly promoted.
        buys = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM transactions WHERE transaction_type = 'buy' AND source = 'excel'"
            ).fetchall()
        ]

        if table_exists(conn, "matches"):
            matches = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT m.*, s.platform AS sell_platform,
                           s.transfer_status AS sell_transfer_status,
                           s.purchase_date AS sell_purchase_date
                    FROM matches m
                    JOIN transactions s ON s.id = m.sell_id
                    WHERE s.source = 'excel'
                    """
                ).fetchall()
            ]
        else:
            matches = []

        # Purely informational: scraped emails not yet promoted into the
        # canonical dataset. Never feeds matching/profit — see match.py.
        unpromoted = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM transactions WHERE source = 'email' AND promoted = 0"
            ).fetchall()
        ]
        excel_rows = [
            dict(r) for r in conn.execute("SELECT * FROM transactions WHERE source = 'excel'").fetchall()
        ]
    finally:
        conn.close()

    # Rows already recorded by hand in the sheet aren't pending; the
    # dashboard lists them separately. So are rows the user marked as
    # recorded from the dashboard (manual_marks.csv).
    recorded = find_excel_matches(unpromoted, excel_rows)
    marked = load_marks()
    pending = [
        p for p in unpromoted if p["id"] not in recorded and str(p["raw_email_uid"]) not in marked
    ]
    manual_count = len(unpromoted) - len(recorded) - len(pending)
    return buys, matches, pending, len(recorded), manual_count


def write_header(ws, columns) -> None:
    bold = Font(bold=True)
    for idx, name in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=idx, value=name)
        cell.font = bold


def write_row(ws, row_num, columns, data, date_cols, currency_cols, percent_cols) -> None:
    for idx, name in enumerate(columns, start=1):
        value = data.get(name)
        cell = ws.cell(row=row_num, column=idx, value=value)
        if name in date_cols:
            if value is not None:
                cell.number_format = DATE_FORMAT
        elif name in currency_cols:
            cell.number_format = CURRENCY_FORMAT
        elif name in percent_cols:
            cell.number_format = PERCENT_FORMAT


def build_bl_rows(buys, matches):
    # A buy can now be split across multiple sells (see match.py), so a buy_id
    # may have several match rows. Date Sold reflects when the position was
    # fully liquidated: the latest sell date among all of that buy's matches.
    matches_by_buy_id = {}
    for m in matches:
        matches_by_buy_id.setdefault(m["buy_id"], []).append(m)

    rows = []
    for buy in buys:
        buy_matches = matches_by_buy_id.get(buy["id"], [])
        sell_dates = [
            d for d in (parse_date(m["sell_purchase_date"]) for m in buy_matches) if d is not None
        ]
        date_sold = max(sell_dates) if sell_dates else None
        rows.append(
            {
                "Status": None,
                "Event": buy["artist_or_event"],
                "Event Date": parse_date(buy["event_date"]),
                "Date Purchased": parse_date(buy["purchase_date"]),
                "Days to Sell": None,
                "Venue": buy["venue"],
                "$ per Tix": buy["price_per_ticket"],
                "Quantity": buy["quantity"],
                "Total Cost": buy["total_price"],
                "Date Sold": date_sold,
                "Paid out?": None,
                "Payout Date": None,
                "Account": None,
                "Lysted?": None,
            }
        )
    return rows


def build_sl_rows(matches):
    rows = []
    for i, m in enumerate(matches, start=1):
        qty = m["quantity"]
        purchase_cost = m["purchase_cost"]
        gross_sale = m["gross_sale"]
        purchase_price_per_ticket = (
            purchase_cost / qty if purchase_cost is not None and qty else None
        )
        sell_price_per_ticket = gross_sale / qty if gross_sale is not None and qty else None

        rows.append(
            {
                "SID": i,
                "Event": m["event"],
                "Venue": m["venue"],
                "Event Date": parse_date(m["event_date"]),
                "Date Sold": parse_date(m["sell_purchase_date"]),
                "Days Until Event": None,
                "Tickets Sold": qty,
                "Purchase Price (per ticket)": purchase_price_per_ticket,
                "Sell Price (per ticket)": sell_price_per_ticket,
                "Marketplace": m["sell_platform"],
                "Transferred?": m["sell_transfer_status"],
                "Total Sale After Fees": gross_sale,
                "Gross Sale": gross_sale,
                "Net Profit": m["net_profit"],
                "ROI": m["roi"],
            }
        )
    return rows


def build_pending_rows(pending):
    rows = []
    for p in pending:
        rows.append(
            {
                "Type": p["transaction_type"],
                "Event": p["artist_or_event"],
                "Venue": p["venue"],
                "Event Date": parse_date(p["event_date"]),
                "Purchase Date": parse_date(p["purchase_date"]),
                "Quantity": p["quantity"],
                "Price per Ticket": p["price_per_ticket"],
                "Total Price": p["total_price"],
                "Platform": p["platform"],
                "Needs Review": bool(p["needs_review"]),
                "Review Reason": p["review_reason"],
            }
        )
    return rows


def build_workbook(bl_rows, sl_rows, pending_rows):
    if EXPORT_PATH.exists():
        wb = load_workbook(EXPORT_PATH)
    else:
        wb = Workbook()
        wb.remove(wb.active)

    for name in ("BL", "SL", "Pending"):
        if name in wb.sheetnames:
            del wb[name]

    bl_ws = wb.create_sheet("BL")
    sl_ws = wb.create_sheet("SL")
    pending_ws = wb.create_sheet("Pending")

    write_header(bl_ws, BL_COLUMNS)
    for row_num, row in enumerate(bl_rows, start=2):
        write_row(bl_ws, row_num, BL_COLUMNS, row, BL_DATE_COLS, BL_CURRENCY_COLS, BL_PERCENT_COLS)

    write_header(sl_ws, SL_COLUMNS)
    for row_num, row in enumerate(sl_rows, start=2):
        write_row(sl_ws, row_num, SL_COLUMNS, row, SL_DATE_COLS, SL_CURRENCY_COLS, SL_PERCENT_COLS)

    write_header(pending_ws, PENDING_COLUMNS)
    for row_num, row in enumerate(pending_rows, start=2):
        write_row(
            pending_ws,
            row_num,
            PENDING_COLUMNS,
            row,
            PENDING_DATE_COLS,
            PENDING_CURRENCY_COLS,
            PENDING_PERCENT_COLS,
        )

    wb.save(EXPORT_PATH)


def main() -> None:
    buys, matches, pending, recorded_count, manual_count = load_data()
    bl_rows = build_bl_rows(buys, matches)
    sl_rows = build_sl_rows(matches)
    pending_rows = build_pending_rows(pending)

    build_workbook(bl_rows, sl_rows, pending_rows)

    print("--- Export summary ---")
    print(f"Rows written to BL:      {len(bl_rows)}")
    print(f"Rows written to SL:      {len(sl_rows)}")
    print(f"Rows written to Pending: {len(pending_rows)}")
    print(f"Already in Excel (left out of Pending): {recorded_count}")
    print(f"Manually marked (left out of Pending):  {manual_count}")
    print(f"File: {EXPORT_PATH}")


if __name__ == "__main__":
    main()
