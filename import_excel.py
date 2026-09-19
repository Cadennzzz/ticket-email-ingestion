"""
One-time historical import from the existing Working tickets copy.xlsm
workbook into transactions.db.

Read-only: this script never calls .save() on the workbook and never
writes back to Working tickets copy.xlsm in any way.

Column positions are read dynamically from each sheet's header row (row 2)
rather than hardcoded, since this workbook's layout has changed between
versions.

Safe to re-run: save_transaction() dedupes on raw_email_uid via
INSERT OR IGNORE, so already-imported rows are skipped on subsequent runs.

Run with:
    python import_excel.py
"""

import datetime
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

from db import is_processed, save_transaction

SOURCE_PATH = Path(__file__).parent / "Working tickets copy.xlsm"

HEADER_ROW = 2


def read_header_map(ws):
    """Map column name -> 0-based index from the sheet's header row."""
    rows_iter = ws.iter_rows(min_row=HEADER_ROW, values_only=True)
    header = next(rows_iter)
    col_map = {}
    for idx, name in enumerate(header):
        if name is not None:
            col_map[str(name).strip()] = idx
    return col_map, rows_iter


def get(row, col_map, name):
    idx = col_map.get(name)
    if idx is None or idx >= len(row):
        return None
    return row[idx]


def to_date_str(value):
    if value is None or value == "":
        return None
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.strftime("%Y-%m-%d")
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.strftime("%Y-%m-%d")


def to_float(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).replace("$", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def to_int(value):
    f = to_float(value)
    return int(f) if f is not None else None


def to_transfer_status(value):
    if isinstance(value, bool):
        return "transferred" if value else None
    if value is None or value == "":
        return None
    return str(value).strip().lower()


def import_bl(ws):
    col_map, rows_iter = read_header_map(ws)
    read_count = 0
    inserted = 0
    skipped = 0

    for row_num, row in enumerate(rows_iter, start=HEADER_ROW + 1):
        event = get(row, col_map, "Event")
        if event is None or str(event).strip() == "":
            continue

        read_count += 1
        uid = f"excel-bl-{row_num}"
        if is_processed(uid):
            skipped += 1
            continue

        data = {
            "is_ticket_transaction": True,
            "transaction_type": "buy",
            "platform": get(row, col_map, "Account"),
            "order_id": None,
            "artist_or_event": str(event).strip(),
            "venue": get(row, col_map, "Venue"),
            "event_date": to_date_str(get(row, col_map, "Event Date")),
            "event_time": None,
            "purchase_date": to_date_str(get(row, col_map, "Date Purchased")),
            "ticket_type": None,
            "section": None,
            "row": None,
            "seat": None,
            "quantity": to_int(get(row, col_map, "Quantity")),
            "price_per_ticket": to_float(get(row, col_map, "$ per Tix")),
            "total_price": to_float(get(row, col_map, "Total Cost")),
            "fees": None,
            "currency": None,
            "transfer_status": None,
            "confirmation_number": None,
            "needs_review": False,
            "review_reason": None,
            "raw_email_uid": uid,
            "source": "excel",
        }
        save_transaction(data)
        inserted += 1

    return read_count, inserted, skipped


def import_sl(ws):
    col_map, rows_iter = read_header_map(ws)
    read_count = 0
    inserted = 0
    skipped = 0

    for row in rows_iter:
        sid = get(row, col_map, "SID")
        if sid is None or str(sid).strip() == "":
            continue

        read_count += 1
        uid = f"excel-sl-{str(sid).strip()}"
        if is_processed(uid):
            skipped += 1
            continue

        data = {
            "is_ticket_transaction": True,
            "transaction_type": "sell",
            "platform": get(row, col_map, "Marketplace"),
            "order_id": None,
            "artist_or_event": str(get(row, col_map, "Event") or "").strip() or None,
            "venue": get(row, col_map, "Venue"),
            "event_date": to_date_str(get(row, col_map, "Event Date")),
            "event_time": None,
            "purchase_date": to_date_str(get(row, col_map, "Date Sold")),
            "ticket_type": None,
            "section": None,
            "row": None,
            "seat": None,
            "quantity": to_int(get(row, col_map, "Tickets Sold")),
            "price_per_ticket": to_float(get(row, col_map, "Sell Price (per ticket)")),
            "total_price": to_float(get(row, col_map, "Gross Sale")),
            "fees": None,
            "currency": None,
            "transfer_status": to_transfer_status(get(row, col_map, "Transferred?")),
            "confirmation_number": None,
            "needs_review": False,
            "review_reason": None,
            "raw_email_uid": uid,
            "source": "excel",
        }
        save_transaction(data)
        inserted += 1

    return read_count, inserted, skipped


def main() -> None:
    wb = load_workbook(SOURCE_PATH, keep_vba=True, read_only=True, data_only=True)
    try:
        bl_read, bl_inserted, bl_skipped = import_bl(wb["BL"])
        sl_read, sl_inserted, sl_skipped = import_sl(wb["SL"])
    finally:
        wb.close()

    print("--- Import summary ---")
    print(f"BL rows read:     {bl_read}")
    print(f"SL rows read:     {sl_read}")
    print(f"Rows inserted:    {bl_inserted + sl_inserted}")
    print(f"Rows skipped:     {bl_skipped + sl_skipped}")


if __name__ == "__main__":
    main()
