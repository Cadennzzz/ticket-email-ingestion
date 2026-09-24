"""
Main ingestion script.

Scans the inbox via IMAP, filters to allowlisted ticket-platform senders,
skips anything already in the DB (saved, or recorded in skipped_emails as
deliberately not saved), sends the rest to Gemini for structured
extraction, and stores confirmed ticket transactions in transactions.db.

If extraction finds no price in the email body, falls back to fetching a
linked ticket/order page (PDF or HTML) from the body and re-extracting
once with that content appended — see try_link_fallback.

Usage:
    python ingest.py [--limit N]
"""

import argparse
import html
import io
import os
import re
import sys
from datetime import timezone
from typing import Optional

import requests
from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from imap_tools import AND, MailBox
from pypdf import PdfReader
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from allowlist import is_allowlisted, platform_for_sender
from db import (
    delete_email_row,
    find_order,
    find_same_event,
    is_processed,
    is_skipped,
    record_skip,
    save_transaction,
    unmatched_transfer_rows,
)
from extraction_schema import EXTRACTION_PROMPT, TicketTransaction

load_dotenv()

GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

MODEL_NAME = "gemini-3.5-flash-lite"

_TAG_RE = re.compile(r"<[^>]+>")
# <style>/<script>/<head> contents and HTML comments (Outlook conditional
# CSS) aren't visible text; left in, StubHub emails reach the LLM as ~29k
# chars of mostly CSS.
_INVISIBLE_RE = re.compile(r"<(style|script|head)\b.*?</\1\s*>|<!--.*?-->", re.IGNORECASE | re.DOTALL)
_URL_RE = re.compile(r'https?://[^\s<>"\')\]]+')

# Fallback link-fetch, used when an email mentions a price only on a linked
# order/ticket page (e.g. a "view your tickets" PDF) rather than in the body.
MAX_LINK_CANDIDATES = 3
LINK_FETCH_TIMEOUT = 8
LINK_FETCH_USER_AGENT = "Mozilla/5.0 (compatible; ticket-ingestion-bot/1.0)"


_HREF_RE = re.compile(r'<a\b[^>]*?\bhref\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)

# Below this length a text/plain part is treated as a stub rather than the
# real content — e.g. Lysted sends "Text content not supported for this
# email (yet!)" as the plain part and puts the whole sale in the HTML.
MIN_PLAIN_TEXT_CHARS = 200


def html_to_text(raw_html: str) -> str:
    """Crude fallback: drop non-visible blocks, strip tags, unescape entities."""
    text = _INVISIBLE_RE.sub(" ", raw_html)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def email_body_text(plain: str, raw_html: str) -> str:
    """Prefer the text/plain part, unless it's missing or a placeholder stub
    and there's an HTML part to fall back to."""
    plain = (plain or "").strip()
    if raw_html and len(plain) < MIN_PLAIN_TEXT_CHARS:
        return html_to_text(raw_html)
    return plain


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, genai_errors.APIError) and exc.code in (429, 503)


@retry(
    retry=retry_if_exception(_is_retryable),
    wait=wait_exponential(multiplier=1, min=1, max=60),
    stop=stop_after_attempt(5),
    reraise=True,
)
def extract_transaction(client: genai.Client, sender: str, subject: str, received_date: str, body: str) -> TicketTransaction:
    prompt = EXTRACTION_PROMPT.format(
        sender=sender,
        subject=subject,
        received_date=received_date,
        email_body=body,
    )
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=TicketTransaction,
        ),
    )
    return TicketTransaction.model_validate_json(response.text)


def extract_candidate_urls(body: str, raw_html: str = "", limit: int = MAX_LINK_CANDIDATES) -> list:
    """
    Return up to `limit` distinct http(s) URLs from the email: hrefs from
    the raw HTML (HTML-only emails lose their links in html_to_text), then
    bare URLs in the text body. Direct .pdf links go first since those are
    usually the ticket itself; otherwise order of appearance is kept.
    """
    found = [html.unescape(href).strip() for href in _HREF_RE.findall(raw_html or "")]
    found += [url.rstrip(').,;\'"') for url in _URL_RE.findall(body or "")]  # trailing punctuation often glued to a link in prose

    seen = set()
    urls = []
    for url in found:
        if url.lower().startswith(("http://", "https://")) and url not in seen:
            seen.add(url)
            urls.append(url)

    urls.sort(key=lambda u: not u.lower().split("?", 1)[0].endswith(".pdf"))
    return urls[:limit]


def fetch_linked_text(url: str) -> Optional[str]:
    """
    Fetch `url` and return extracted text if it looks like a ticket/order
    page (PDF or HTML). Returns None on any failure — bad status, timeout,
    connection error, unrecognized content type, unparseable PDF — never
    raises.
    """
    try:
        response = requests.get(
            url,
            timeout=LINK_FETCH_TIMEOUT,
            headers={"User-Agent": LINK_FETCH_USER_AGENT},
        )
    except requests.RequestException:
        return None

    if not response.ok:
        return None

    content_type = response.headers.get("Content-Type", "").lower()

    if "application/pdf" in content_type or url.lower().split("?", 1)[0].endswith(".pdf"):
        try:
            reader = PdfReader(io.BytesIO(response.content))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception:
            return None
        return text.strip() or None

    if "text/html" in content_type:
        return html_to_text(response.text) or None

    return None


def try_link_fallback(client: genai.Client, sender: str, subject: str, received_date: str, body: str, raw_html: str = "") -> Optional[TicketTransaction]:
    """
    Called when the initial extraction found no price. Looks for links in
    the email body and tries fetching each candidate (up to
    MAX_LINK_CANDIDATES) in order until one yields usable text, then
    re-runs extraction exactly once with that text appended to the
    original body. Never issues more than one Gemini call, even if that
    call still comes back without a price — this is a single fallback
    attempt, not an exhaustive retry across every link.

    Returns the re-extracted TicketTransaction if it found a price,
    otherwise None (caller should keep the original result).
    """
    for url in extract_candidate_urls(body, raw_html):
        linked_text = fetch_linked_text(url)
        if not linked_text:
            continue

        augmented_body = f"{body}\n\nLinked ticket page content:\n{linked_text}"
        fallback_result = extract_transaction(
            client=client,
            sender=sender,
            subject=subject,
            received_date=received_date,
            body=augmented_body,
        )
        if fallback_result.total_price is not None or fallback_result.price_per_ticket is not None:
            return fallback_result
        return None  # fetched content but still no price — don't try more links

    return None


def email_header_fields(msg) -> dict:
    """
    sender / sender_name / recipient / received_date for a message.

    The inbox is a forwarding bin, so `To` is what identifies which of the
    real accounts received the email. If `To` is empty (BCC), fall back to
    the first Delivered-To that isn't the bin itself. received_date is ISO
    8601 in UTC; imap_tools returns 1900-01-01 for unparseable dates, which
    is stored as NULL.
    """
    recipients = [a.strip().lower() for a in msg.to if a and a.strip()]
    if not recipients:
        bin_address = (GMAIL_USER or "").lower()
        delivered = [
            a.strip().lower()
            for a in msg.headers.get("delivered-to", ())
            if a and a.strip().lower() != bin_address
        ]
        recipients = delivered[:1]

    received_date = None
    if msg.date and msg.date.year > 1900:
        date = msg.date if msg.date.tzinfo else msg.date.replace(tzinfo=timezone.utc)
        received_date = date.astimezone(timezone.utc).isoformat(timespec="seconds")

    return {
        "sender": (msg.from_ or "").strip().lower() or None,
        "sender_name": (msg.from_values.name.strip() or None) if msg.from_values else None,
        "recipient": ", ".join(recipients) or None,
        "received_date": received_date,
    }


def _skip(stats: dict, key: str, msg, why: str) -> None:
    """Count a skip and record it in skipped_emails so later runs don't
    send this email to Gemini again. `key` is the stats key, "skipped_<reason>"."""
    print(f"  Skipping uid={msg.uid}: {why}")
    stats[key] += 1
    record_skip(msg.uid, key[len("skipped_"):], why, msg.subject)


def _flag(result: TicketTransaction, reason: str) -> None:
    result.needs_review = True
    result.review_reason = result.review_reason or reason


def process_message(client: genai.Client, msg, stats: dict) -> Optional[dict]:
    """
    Extract one allowlisted, not-yet-processed message. Returns the row to
    save, or None if the email isn't the authoritative record of a
    transaction. Updates `stats` in place.

    A purchase is recorded from its receipt; a sale from the email saying
    it sold. Listings, delistings, and follow-ups (delivered/ready notices,
    transfers that deliver an already-saved purchase or sale) are skipped.
    Anything the model can't place is saved with needs_review set.

    Skipped emails are recorded in skipped_emails. If the returned row
    carries "replaces_id", save it with save_result(), which removes the
    lesser row it supersedes.
    """
    body = email_body_text(msg.text, msg.html)

    stats["sent_to_llm"] += 1
    result = extract_transaction(
        client=client,
        sender=msg.from_,
        subject=msg.subject,
        received_date=str(msg.date),
        body=body,
    )

    if not result.is_ticket_transaction:
        _skip(stats, "skipped_not_transaction", msg, "not a ticket transaction")
        return None

    kind = result.email_kind

    # Listing confirmations ("You listed ... tickets") aren't
    # sales — the matching "sold" email is what counts.
    if kind == "sale_listing" or result.transfer_status == "listed":
        _skip(stats, "skipped_listing", msg, "listing, not a sale")
        return None

    # "You deleted your listing" emails aren't sales either.
    if kind == "sale_delisting" or result.transfer_status == "delisted":
        _skip(stats, "skipped_delisting", msg, "listing removed, not a sale")
        return None

    platform = platform_for_sender(msg.from_) or result.platform
    replaces_id = replaces_uid = None

    # Confirmation + "tickets delivered" emails for one order extract to the
    # same transaction. Keep one row per order; a receipt supersedes a row
    # saved from a follow-up (e.g. "tickets ready" that shows the subtotal
    # without fees).
    if platform and result.order_id and result.transaction_type:
        existing = find_order(platform, result.order_id, result.transaction_type)
        if existing is not None:
            upgradable = (
                kind == "purchase_receipt"
                and existing["email_kind"] == "purchase_update"
                and existing["source"] == "email"
                and not existing["promoted"]
            )
            if not upgradable:
                _skip(stats, "skipped_duplicate_order", msg,
                      f"{platform} order {result.order_id} already saved as id={existing['id']}")
                return None
            replaces_id = existing["id"]
            replaces_uid = existing["raw_email_uid"]

    has_price = result.total_price is not None or result.price_per_ticket is not None
    if not has_price and kind != "transfer_out":
        stats["link_fallback_attempted"] += 1
        try:
            fallback_result = try_link_fallback(
                client=client,
                sender=msg.from_,
                subject=msg.subject,
                received_date=str(msg.date),
                body=body,
                raw_html=msg.html or "",
            )
        except Exception as e:
            fallback_result = None
            print(f"  Link-fetch fallback errored for uid={msg.uid}: {e}")

        if fallback_result is not None:
            print(f"  Link-fetch fallback recovered price for uid={msg.uid}")
            stats["link_fallback_recovered_price"] += 1
            fallback_result.email_kind = kind
            result = fallback_result
            has_price = True

    # A transfer email has no order number and no price of its own. One that
    # matches a saved purchase/sale (same event, date, quantity) is just
    # delivering it; one that doesn't may be the only trace of an
    # off-platform deal. Checked after the link fallback, so a per-ticket
    # email whose price is in the ticket PDF (Fourvenues) is never a transfer.
    if kind in ("transfer_out", "purchase_update") and not result.order_id and not has_price:
        match_id = None
        if result.transaction_type and result.event_date and result.quantity:
            match_id = find_same_event(
                result.transaction_type, result.event_date, result.quantity, result.artist_or_event
            )
        if match_id is not None:
            _skip(stats, "skipped_transfer", msg, f"transfer for already-saved id={match_id}")
            return None
        if kind == "transfer_out":
            _flag(result, "transfer out with no sale price — possibly an off-platform sale")

    if kind == "sale_completed" and not has_price and result.payout_amount is None:
        _flag(result, "sale email states neither a sale price nor a payout")
    elif kind == "purchase_update" and not has_price:
        _flag(result, "purchase follow-up with no receipt or price found")
    elif kind is None or kind == "unclear":
        _flag(result, "email type unclear — not clearly a receipt, sale, listing, or transfer")

    result.raw_email_uid = msg.uid
    result.platform = platform
    data = result.model_dump()
    # Scraped transactions stay isolated from the canonical
    # (source='excel') dataset until explicitly promoted —
    # matching/export/dashboard metrics never see these.
    data["source"] = "email"
    data.update(email_header_fields(msg))
    data["replaces_id"] = replaces_id
    data["replaces_uid"] = replaces_uid
    return data


def save_result(data: dict) -> None:
    """Save a row from process_message, first removing the row it supersedes
    (and recording that row's email as skipped, so it isn't re-extracted)."""
    if data.get("replaces_id") is not None:
        if delete_email_row(data["replaces_id"]):
            record_skip(data["replaces_uid"], "duplicate_order",
                        f"superseded by receipt uid={data['raw_email_uid']}")
        print(f"  uid={data['raw_email_uid']} replaces id={data['replaces_id']}")
    save_transaction(data)


def drop_matched_transfers(stats: dict) -> None:
    """
    Remove saved transfer rows that now match a purchase/sale. A transfer
    can arrive minutes before the "sold" email it delivers (or in an earlier
    run), so at the time it was processed there was nothing to match.
    """
    for row in unmatched_transfer_rows():
        if not (row["transaction_type"] and row["event_date"] and row["quantity"]):
            continue
        match_id = find_same_event(
            row["transaction_type"], row["event_date"], row["quantity"], row["artist_or_event"]
        )
        if match_id is not None and delete_email_row(row["id"]):
            record_skip(row["raw_email_uid"], "transfer", f"transfer for already-saved id={match_id} (dropped after saving)")
            print(f"  Dropped transfer row id={row['id']} (uid={row['raw_email_uid']}): delivers id={match_id}")
            stats["dropped_matched_transfer"] += 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan inbox and extract ticket transactions.")
    parser.add_argument("--limit", type=int, default=100, help="Max number of messages to fetch (default: 100)")
    args = parser.parse_args()

    missing = [
        name
        for name, val in [
            ("GMAIL_USER", GMAIL_USER),
            ("GMAIL_APP_PASSWORD", GMAIL_APP_PASSWORD),
            ("GEMINI_API_KEY", GEMINI_API_KEY),
        ]
        if not val
    ]
    if missing:
        print(f"Missing env vars: {', '.join(missing)}. Check .env.")
        sys.exit(1)

    client = genai.Client(api_key=GEMINI_API_KEY)

    stats = {
        "scanned": 0,
        "skipped_not_allowlisted": 0,
        "skipped_already_processed": 0,
        "skipped_recorded": 0,
        "sent_to_llm": 0,
        "saved": 0,
        "needs_review": 0,
        "errors": 0,
        "link_fallback_attempted": 0,
        "link_fallback_recovered_price": 0,
        "skipped_listing": 0,
        "skipped_delisting": 0,
        "skipped_duplicate_order": 0,
        "skipped_transfer": 0,
        "skipped_not_transaction": 0,
        "dropped_matched_transfer": 0,
    }

    with MailBox("imap.gmail.com").login(GMAIL_USER, GMAIL_APP_PASSWORD) as mailbox:
        # Fetch the newest N, then process oldest first: a receipt normally
        # arrives before its "delivered" notices and a sale before its
        # transfer, so the authoritative email is usually seen first.
        messages = sorted(
            mailbox.fetch(AND(all=True), limit=args.limit, reverse=True),
            key=lambda m: int(m.uid),
        )

        for msg in messages:
            stats["scanned"] += 1
            try:
                if not is_allowlisted(msg.from_):
                    stats["skipped_not_allowlisted"] += 1
                    continue

                if is_processed(msg.uid):
                    stats["skipped_already_processed"] += 1
                    continue

                if is_skipped(msg.uid):
                    stats["skipped_recorded"] += 1
                    continue

                data = process_message(client, msg, stats)
                if data is None:
                    continue
                save_result(data)
                stats["saved"] += 1
                if data["needs_review"]:
                    stats["needs_review"] += 1

            except Exception as e:
                stats["errors"] += 1
                print(f"Error processing message uid={getattr(msg, 'uid', '?')}: {e}")
                continue

    drop_matched_transfers(stats)

    print("\n--- Ingestion summary ---")
    print(f"Total emails scanned:      {stats['scanned']}")
    print(f"Skipped (not allowlisted): {stats['skipped_not_allowlisted']}")
    print(f"Skipped (already processed): {stats['skipped_already_processed']}")
    print(f"Skipped (recorded skip):   {stats['skipped_recorded']}")
    print(f"Sent to LLM:               {stats['sent_to_llm']}")
    print(f"Skipped (listing only):    {stats['skipped_listing']}")
    print(f"Skipped (delisting):       {stats['skipped_delisting']}")
    print(f"Skipped (duplicate order): {stats['skipped_duplicate_order']}")
    print(f"Skipped (transfer of saved): {stats['skipped_transfer']}")
    print(f"Skipped (not a transaction): {stats['skipped_not_transaction']}")
    print(f"Dropped (transfer matched later): {stats['dropped_matched_transfer']}")
    print(f"Saved as transactions:     {stats['saved']}")
    print(f"Flagged needs_review:      {stats['needs_review']}")
    print(f"Errors:                    {stats['errors']}")
    print(f"Link-fetch fallback tried: {stats['link_fallback_attempted']}")
    print(f"Link-fetch recovered price: {stats['link_fallback_recovered_price']}")


if __name__ == "__main__":
    main()
