"""
Manual "already recorded in Excel" marks for scraped email rows.

Some emails are entered into the working sheet under a name or structure
crosscheck.find_excel_matches can't link. Rather than loosen that matcher,
the dashboard lets the user mark such rows by hand; they then leave Pending
(dashboard and the export's Pending sheet) the same way an auto-matched row
does. Nothing in transactions.db is changed.

Marks live in manual_marks.csv, keyed by raw_email_uid, not in the DB:
reprocess.py deletes and re-inserts rows (so ids change), and the pipeline
commits transactions.db, which would clobber or conflict with local writes.
A small text file survives both and diffs cleanly in git.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

MARKS_PATH = Path(__file__).resolve().parent / "manual_marks.csv"
FIELDS = ["raw_email_uid", "marked_at", "note"]


def load_marks(path: Path = MARKS_PATH) -> dict[str, dict]:
    """Return {raw_email_uid: row} for every marked uid ({} if no file)."""
    if not path.exists():
        return {}
    with path.open(newline="") as f:
        return {r["raw_email_uid"]: r for r in csv.DictReader(f) if r.get("raw_email_uid")}


def _save(marks: dict[str, dict], path: Path) -> None:
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        for uid in sorted(marks, key=lambda u: (len(u), u)):
            writer.writerow(marks[uid])
    tmp.replace(path)


def mark(uids, note: str = "", path: Path = MARKS_PATH) -> None:
    marks = load_marks(path)
    now = datetime.now().isoformat(timespec="seconds")
    for uid in map(str, uids):
        marks.setdefault(uid, {"raw_email_uid": uid, "marked_at": now, "note": note})
    _save(marks, path)


def unmark(uids, path: Path = MARKS_PATH) -> None:
    marks = load_marks(path)
    for uid in map(str, uids):
        marks.pop(uid, None)
    _save(marks, path)
