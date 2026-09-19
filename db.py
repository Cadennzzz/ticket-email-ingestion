"""
SQLite storage for extracted ticket transactions.

One row per TicketTransaction, keyed for dedupe on raw_email_uid.
"""

import sqlite3
from pathlib import Path

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
    currency TEXT,
    transfer_status TEXT,
    confirmation_number TEXT,
    needs_review BOOLEAN NOT NULL DEFAULT 0,
    review_reason TEXT,
    raw_email_uid TEXT UNIQUE NOT NULL,
    processed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    source TEXT NOT NULL DEFAULT 'email',
    promoted BOOLEAN NOT NULL DEFAULT 0
);
"""

# Columns added after the original schema shipped. Each is added via
# ALTER TABLE on existing databases the first time get_connection() sees
# them missing (see _migrate). New databases get them from SCHEMA above
# directly, so the ALTER is a no-op there.
_MIGRATIONS = [
    ("source", "TEXT NOT NULL DEFAULT 'email'"),
    ("promoted", "BOOLEAN NOT NULL DEFAULT 0"),
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
    "currency",
    "transfer_status",
    "confirmation_number",
    "needs_review",
    "review_reason",
    "raw_email_uid",
    "source",
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
