"""
Main ingestion script.

Scans the inbox via IMAP, filters to allowlisted ticket-platform senders,
skips anything already in the DB, sends the rest to Gemini for structured
extraction, and stores confirmed ticket transactions in transactions.db.

Usage:
    python ingest.py [--limit N]
"""

import argparse
import html
import os
import re
import sys

from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from imap_tools import AND, MailBox
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from allowlist import is_allowlisted
from db import is_processed, save_transaction
from extraction_schema import EXTRACTION_PROMPT, TicketTransaction

load_dotenv()

GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

MODEL_NAME = "gemini-3.5-flash-lite"

_TAG_RE = re.compile(r"<[^>]+>")


def html_to_text(raw_html: str) -> str:
    """Crude fallback: strip tags and unescape entities."""
    text = _TAG_RE.sub(" ", raw_html)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


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

                body = msg.text or html_to_text(msg.html or "")

                stats["sent_to_llm"] += 1
                result = extract_transaction(
                    client=client,
                    sender=msg.from_,
                    subject=msg.subject,
                    received_date=str(msg.date),
                    body=body,
                )

                if not result.is_ticket_transaction:
                    continue

                result.raw_email_uid = msg.uid
                data = result.model_dump()
                # Scraped transactions stay isolated from the canonical
                # (source='excel') dataset until explicitly promoted —
                # matching/export/dashboard metrics never see these.
                data["source"] = "email"
                save_transaction(data)
                stats["saved"] += 1
                if result.needs_review:
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
    print(f"Saved as transactions:     {stats['saved']}")
    print(f"Flagged needs_review:      {stats['needs_review']}")
    print(f"Errors:                    {stats['errors']}")


if __name__ == "__main__":
    main()
