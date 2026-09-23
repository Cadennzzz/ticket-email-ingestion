"""
Main ingestion script.

Scans the inbox via IMAP, filters to allowlisted ticket-platform senders,
skips anything already in the DB, sends the rest to Gemini for structured
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
from db import is_processed, save_transaction
from extraction_schema import EXTRACTION_PROMPT, TicketTransaction

load_dotenv()

GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

MODEL_NAME = "gemini-3.5-flash-lite"

_TAG_RE = re.compile(r"<[^>]+>")
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
    """Crude fallback: strip tags and unescape entities."""
    text = _TAG_RE.sub(" ", raw_html)
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


def process_message(client: genai.Client, msg, stats: dict) -> Optional[dict]:
    """
    Extract one allowlisted, not-yet-processed message. Returns the row to
    save, or None if it isn't a saveable transaction (not a ticket email,
    or a listing-only confirmation). Updates `stats` in place.
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
        return None

    # Listing confirmations ("You listed ... tickets") aren't
    # sales — the matching "sold" email is what counts.
    if result.transfer_status == "listed":
        stats["skipped_listing"] += 1
        return None

    if result.total_price is None and result.price_per_ticket is None:
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
            result = fallback_result

    result.raw_email_uid = msg.uid
    result.platform = platform_for_sender(msg.from_) or result.platform
    data = result.model_dump()
    # Scraped transactions stay isolated from the canonical
    # (source='excel') dataset until explicitly promoted —
    # matching/export/dashboard metrics never see these.
    data["source"] = "email"
    return data


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
        "sent_to_llm": 0,
        "saved": 0,
        "needs_review": 0,
        "errors": 0,
        "link_fallback_attempted": 0,
        "link_fallback_recovered_price": 0,
        "skipped_listing": 0,
    }

    with MailBox("imap.gmail.com").login(GMAIL_USER, GMAIL_APP_PASSWORD) as mailbox:
        messages = mailbox.fetch(AND(all=True), limit=args.limit, reverse=True)

        for msg in messages:
            stats["scanned"] += 1
            try:
                if not is_allowlisted(msg.from_):
                    stats["skipped_not_allowlisted"] += 1
                    continue

                if is_processed(msg.uid):
                    stats["skipped_already_processed"] += 1
                    continue

                data = process_message(client, msg, stats)
                if data is None:
                    continue
                save_transaction(data)
                stats["saved"] += 1
                if data["needs_review"]:
                    stats["needs_review"] += 1

            except Exception as e:
                stats["errors"] += 1
                print(f"Error processing message uid={getattr(msg, 'uid', '?')}: {e}")
                continue

    print("\n--- Ingestion summary ---")
    print(f"Total emails scanned:      {stats['scanned']}")
    print(f"Skipped (not allowlisted): {stats['skipped_not_allowlisted']}")
    print(f"Skipped (already processed): {stats['skipped_already_processed']}")
    print(f"Sent to LLM:               {stats['sent_to_llm']}")
    print(f"Skipped (listing only):    {stats['skipped_listing']}")
    print(f"Saved as transactions:     {stats['saved']}")
    print(f"Flagged needs_review:      {stats['needs_review']}")
    print(f"Errors:                    {stats['errors']}")
    print(f"Link-fetch fallback tried: {stats['link_fallback_attempted']}")
    print(f"Link-fetch recovered price: {stats['link_fallback_recovered_price']}")


if __name__ == "__main__":
    main()
