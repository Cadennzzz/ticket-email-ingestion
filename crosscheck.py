"""
Loose event-name comparison shared by ingestion and the dashboard.

Event names for the same show differ between sources: "Four Tet" (StubHub)
vs "Four Tet - Admissions" (AXS), or "Prospa" (Lysted) vs "Prospa Mission"
(Excel). Two names are treated as the same event when they share at least
one significant word; callers always pair this with matching type, event
date, and quantity/total, so the name check only guards against unrelated
events that happen to coincide on those.
"""

import re
from itertools import combinations

_WORD_RE = re.compile(r"[a-z0-9]+")

# Words that appear across unrelated events and say nothing about which
# show it is.
_GENERIC_WORDS = {
    "the", "and", "an", "of", "at", "in", "on", "with", "for", "to", "by", "vs",
    "present", "presents", "admission", "admissions", "ticket", "tickets",
    "ga", "general", "vip", "event", "live", "tour", "show", "night", "day",
}


def event_words(name) -> set:
    """Significant lowercase words of an event name (3+ chars, not a number,
    not generic)."""
    words = _WORD_RE.findall((name or "").lower())
    return {w for w in words if len(w) >= 3 and not w.isdigit() and w not in _GENERIC_WORDS}


def same_event(a, b) -> bool:
    return bool(event_words(a) & event_words(b))


# --- Email rows already recorded in Excel -----------------------------------

# Totals typed into the sheet are sometimes rounded (Excel 550: $466.00 for
# emails summing to $465.50).
TOTAL_TOLERANCE = 1.00

# Largest group of email rows tried against one Excel row. Keeps the subset
# search trivial; real groups so far are 2-3 rows (one email per ticket).
MAX_GROUP = 6


def _usable(row) -> bool:
    return (
        row.get("transaction_type") is not None
        and row.get("event_date") is not None
        and row.get("quantity") is not None
        and row.get("total_price") is not None
        and row["total_price"] == row["total_price"]  # not NaN
        and row["quantity"] == row["quantity"]
    )


def _compatible(email_row, excel_row) -> bool:
    return (
        email_row["transaction_type"] == excel_row["transaction_type"]
        and email_row["event_date"] == excel_row["event_date"]
        and same_event(email_row.get("artist_or_event"), excel_row.get("artist_or_event"))
    )


def _totals_match(qty, total, excel_row) -> bool:
    return qty == excel_row["quantity"] and abs(total - excel_row["total_price"]) <= TOTAL_TOLERANCE


def find_excel_matches(email_rows: list, excel_rows: list) -> dict:
    """
    Map email row id -> id of the source='excel' row that already records
    it. Rows are dicts with id, transaction_type, event_date, quantity,
    total_price, artist_or_event.

    An email row matches an Excel row with the same buy/sell, event date,
    and a shared event word when quantity is equal and total is within
    TOTAL_TOLERANCE -- either on its own, or as part of a group of email
    rows whose quantities and totals sum to the Excel row's (per-ticket
    emails recorded as one Excel line). Each Excel row covers at most one
    match; 1:1 matches are assigned before groups, and among equally good
    candidates the earliest (lowest id) email rows are used.
    """
    emails = sorted((r for r in email_rows if _usable(r)), key=lambda r: r["id"])
    excels = sorted((r for r in excel_rows if _usable(r)), key=lambda r: r["id"])
    matched = {}
    used_excel = set()

    # 1:1 first: an exact line in the sheet is the strongest evidence.
    for e in emails:
        for x in excels:
            if x["id"] in used_excel or not _compatible(e, x):
                continue
            if _totals_match(e["quantity"], e["total_price"], x):
                matched[e["id"]] = x["id"]
                used_excel.add(x["id"])
                break

    # Then groups of the remaining email rows against remaining Excel rows.
    for x in excels:
        if x["id"] in used_excel:
            continue
        pool = [e for e in emails if e["id"] not in matched and _compatible(e, x)]
        for size in range(2, min(len(pool), MAX_GROUP) + 1):
            group = next(
                (
                    combo for combo in combinations(pool, size)
                    if _totals_match(
                        sum(e["quantity"] for e in combo), sum(e["total_price"] for e in combo), x
                    )
                ),
                None,
            )
            if group:
                for e in group:
                    matched[e["id"]] = x["id"]
                used_excel.add(x["id"])
                break

    return matched
