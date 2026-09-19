"""
Excel export for ticket bookkeeping.

Reads transactions.db (transactions + matches tables, the latter produced
by match.py) and writes/replaces the BL and SL sheets in EmailScrape.xlsx.
Any other sheet in that workbook, and Working_tickets_copy.xlsm, are left
untouched.

Run with:
    python export.py
"""

import sqlite3
from pathlib import Path

import pandas as pd
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font

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
        buys = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM transactions WHERE transaction_type = 'buy'"
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
                    """
                ).fetchall()
            ]
        else:
            matches = []
    finally:
        conn.close()
    return buys, matches


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


def build_workbook(bl_rows, sl_rows):
    if EXPORT_PATH.exists():
        wb = load_workbook(EXPORT_PATH)
    else:
        wb = Workbook()
        wb.remove(wb.active)

    for name in ("BL", "SL"):
        if name in wb.sheetnames:
            del wb[name]

    bl_ws = wb.create_sheet("BL")
    sl_ws = wb.create_sheet("SL")

    write_header(bl_ws, BL_COLUMNS)
    for row_num, row in enumerate(bl_rows, start=2):
        write_row(bl_ws, row_num, BL_COLUMNS, row, BL_DATE_COLS, BL_CURRENCY_COLS, BL_PERCENT_COLS)

    write_header(sl_ws, SL_COLUMNS)
    for row_num, row in enumerate(sl_rows, start=2):
        write_row(sl_ws, row_num, SL_COLUMNS, row, SL_DATE_COLS, SL_CURRENCY_COLS, SL_PERCENT_COLS)

    wb.save(EXPORT_PATH)


def main() -> None:
    buys, matches = load_data()
    bl_rows = build_bl_rows(buys, matches)
    sl_rows = build_sl_rows(matches)

    build_workbook(bl_rows, sl_rows)

    print("--- Export summary ---")
    print(f"Rows written to BL: {len(bl_rows)}")
    print(f"Rows written to SL: {len(sl_rows)}")
    print(f"File: {EXPORT_PATH}")


if __name__ == "__main__":
    main()
