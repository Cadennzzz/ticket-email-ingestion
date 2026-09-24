"""
SQLite storage for extracted ticket transactions.

One row per TicketTransaction, keyed for dedupe on raw_email_uid.
"""

import sqlite3
from pathlib import Path
from typing import Optional

from crosscheck import same_event

DB_PATH = Path(__file__).parent / "transactions.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    is_ticket_transaction BOOLEAN NOT NULL,
    transaction_type TEXT,
    platform TEXT,
    order_id TEXT,
    artist_or_event TEXT,
    venue TEXT,
    event_date TEXT,
    event_time TEXT,
    purchase_date TEXT,
    ticket_type TEXT,
    section TEXT,
    row TEXT,
    seat TEXT,
    quantity INTEGER,
    price_per_ticket REAL,
    total_price REAL,
    fees REAL,
    payout_amount REAL,
    currency TEXT,
    transfer_status TEXT,
    confirmation_number TEXT,
    needs_review BOOLEAN NOT NULL DEFAULT 0,
    review_reason TEXT,
    raw_email_uid TEXT UNIQUE NOT NULL,
    processed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    source TEXT NOT NULL DEFAULT 'email',
    promoted BOOLEAN NOT NULL DEFAULT 0,
    sender TEXT,
    sender_name TEXT,
    recipient TEXT,
    received_date TEXT,
    email_kind TEXT
);
"""

# Columns added after the original schema shipped. Each is added via
# ALTER TABLE on existing databases the first time get_connection() sees
# them missing (see _migrate). New databases get them from SCHEMA above
# directly, so the ALTER is a no-op there.
_MIGRATIONS = [
    ("source", "TEXT NOT NULL DEFAULT 'email'"),
    ("promoted", "BOOLEAN NOT NULL DEFAULT 0"),
    ("payout_amount", "REAL"),
    ("sender", "TEXT"),
    ("sender_name", "TEXT"),
    ("recipient", "TEXT"),
    ("received_date", "TEXT"),
    ("email_kind", "TEXT"),
]

COLUMNS = [
    "is_ticket_transaction",
    "transaction_type",
    "platform",
    "order_id",
    "artist_or_event",
    "venue",
    "event_date",
    "event_time",
    "purchase_date",
    "ticket_type",
    "section",
    "row",
    "seat",
    "quantity",
    "price_per_ticket",
    "total_price",
    "fees",
    "payout_amount",
    "currency",
    "transfer_status",
    "confirmation_number",
    "needs_review",
    "review_reason",
    "raw_email_uid",
    "source",
    "sender",
    "sender_name",
    "recipient",
    "received_date",
    "email_kind",
]


def _migrate(conn: sqlite3.Connection) -> None:
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(transactions)").fetchall()}
    for name, ddl in _MIGRATIONS:
        if name in existing_cols:
            continue
        conn.execute(f"ALTER TABLE transactions ADD COLUMN {name} {ddl}")
        if name == "source":
            # Backfill: rows inserted by import_excel.py are identifiable by
            # their raw_email_uid prefix. Everything else keeps the 'email'
            # default, which is already correct for them.
            conn.execute(
                """
                UPDATE transactions
                SET source = 'excel'
                WHERE raw_email_uid LIKE 'excel-bl-%' OR raw_email_uid LIKE 'excel-sl-%'
                """
            )


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn


def is_processed(uid: str) -> bool:
    """Return True if raw_email_uid is already stored."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT 1 FROM transactions WHERE raw_email_uid = ? LIMIT 1", (uid,)
        )
        return cur.fetchone() is not None
    finally:
        conn.close()


def find_order(platform: str, order_id: str, transaction_type: str) -> Optional[dict]:
    """
    Return the existing row (id, email_kind, source, promoted) for this
    platform + order number + buy/sell, or None. Platforms send several
    emails per order (confirmation, "tickets delivered", ...), each
    extracting to the same transaction.
    """
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            "SELECT id, email_kind, source, promoted FROM transactions WHERE platform = ? "
            "AND TRIM(order_id) = ? AND transaction_type = ? ORDER BY id LIMIT 1",
            (platform, order_id.strip(), transaction_type),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# Rows saved from a transfer email, which has no order number or price of
# its own. They record that tickets moved, not a purchase or sale in
# themselves.
TRANSFER_ROW_SQL = (
    "(source = 'email' AND order_id IS NULL AND total_price IS NULL "
    "AND price_per_ticket IS NULL AND email_kind IN ('transfer_out', 'purchase_update'))"
)


def find_same_event(transaction_type: str, event_date: str, quantity: int, event_name: str) -> Optional[int]:
    """
    Return the id of a saved purchase/sale (any source) with the same
    buy/sell, event date, and quantity whose event name shares a significant
    word, or None. Used to tie a transfer email, which has no order number,
    to the purchase or sale it delivers. Other transfer rows never count.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, artist_or_event FROM transactions WHERE transaction_type = ? "
            f"AND event_date = ? AND quantity = ? AND NOT {TRANSFER_ROW_SQL} ORDER BY id",
            (transaction_type, event_date, quantity),
        ).fetchall()
    finally:
        conn.close()
    return next((row_id for row_id, name in rows if same_event(name, event_name)), None)


def unmatched_transfer_rows() -> list:
    """Unpromoted rows saved from transfer emails, as dicts."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, raw_email_uid, transaction_type, event_date, quantity, artist_or_event "
            f"FROM transactions WHERE promoted = 0 AND {TRANSFER_ROW_SQL} ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_email_row(row_id: int) -> bool:
    """Delete an unpromoted email-sourced row; returns whether one was deleted.
    Never touches source='excel' rows or promoted ones."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM transactions WHERE id = ? AND source = 'email' AND promoted = 0", (row_id,)
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def save_transaction(data: dict) -> None:
    """Insert a transaction row. Ignored (no-op) if raw_email_uid already exists."""
    values = [data.get(col) for col in COLUMNS]
    placeholders = ", ".join("?" for _ in COLUMNS)
    column_list = ", ".join(COLUMNS)

    conn = get_connection()
    try:
        conn.execute(
            f"INSERT OR IGNORE INTO transactions ({column_list}) VALUES ({placeholders})",
            values,
        )
        conn.commit()
    finally:
        conn.close()
