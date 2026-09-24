"""
Ticket-transaction extraction schema + LLM prompt.

Model: gemini-3.5-flash-lite (gemini-2.5-flash-lite was retired for new
users as of this writing; verify current free-tier RPD limits before
relying on volume assumptions).
Use response_schema=TicketTransaction with response_mime_type="application/json"
in the google-genai SDK so Gemini enforces this shape server-side rather than
relying on prompt instructions alone.
"""

from typing import Optional, Literal
from pydantic import BaseModel, Field

# Must cover every label in allowlist.PLATFORM_BY_DOMAIN (ingest.py
# assigns those after extraction), plus "Other" for anything unmapped.
PLATFORMS = (
    "Ticketmaster", "StubHub", "SeatGeek", "AXS", "Vivid Seats", "Gametime",
    "Lysted", "CrowdVolt", "Dice", "Radiate", "Fourvenues", "Paciolan",
    "Belly Up", "Front Gate Tickets", "Insomniac", "Red Rocks", "TicketWeb",
    "Atom Tickets", "TickPick", "See Tickets", "ShowClix", "Prekindle",
    "Tao Group", "Cash or Trade", "Victory Live", "Tixr", "Stagefront",
    "Shotgun", "Club Tickets", "Megatix", "Universe", "Eventbrite",
    "AEG Presents", "Goldenvoice", "Bowery Presents", "Laylo", "Bandsintown",
    "Other",
)


class TicketTransaction(BaseModel):
    # Gate field — check this first. If False, discard the record entirely
    # (don't even look at the other fields).
    is_ticket_transaction: bool = Field(
        description="True if this email is a ticket purchase/sale/transfer/refund confirmation."
    )

    transaction_type: Optional[Literal["buy", "sell"]] = None
    platform: Optional[Literal[PLATFORMS]] = None
    order_id: Optional[str] = None
    artist_or_event: Optional[str] = None
    venue: Optional[str] = None
    event_date: Optional[str] = Field(default=None, description="ISO 8601 date, YYYY-MM-DD")
    event_time: Optional[str] = Field(default=None, description="24hr HH:MM, local to venue")
    purchase_date: Optional[str] = Field(
        default=None, description="ISO 8601 timestamp of the transaction/email, not the event"
    )
    ticket_type: Optional[str] = Field(default=None, description="e.g. GA, reserved, VIP, parking")
    section: Optional[str] = None
    row: Optional[str] = None
    seat: Optional[str] = None
    quantity: Optional[int] = None
    price_per_ticket: Optional[float] = None
    total_price: Optional[float] = None
    fees: Optional[float] = Field(default=None, description="Only if broken out separately from total_price")
    payout_amount: Optional[float] = Field(
        default=None,
        description="Sales only: net amount paid out to the seller after platform fees, only if explicitly stated",
    )
    currency: Optional[str] = Field(default=None, description="ISO 4217, e.g. USD")
    transfer_status: Optional[Literal[
        "transferred", "pending", "listed", "delisted", "sold", "refunded", "cancelled"
    ]] = None
    confirmation_number: Optional[str] = None

    # Review flag — set True whenever transaction_type, total_price, or
    # quantity is ambiguous/uncertain. False negatives (silently dropping
    # a real transaction) are worse than a manual review queue.
    needs_review: bool = False
    review_reason: Optional[str] = Field(
        default=None, description="One short phrase, e.g. 'total_price ambiguous — multiple amounts in email'"
    )

    # NOT extracted by the LLM — populate this yourself from msg.uid after
    # the fact. Keeping it out of the prompt avoids the model hallucinating
    # a plausible-looking UID.
    raw_email_uid: Optional[str] = None


EXTRACTION_PROMPT = """You extract structured data from ticket-marketplace emails \
(Ticketmaster, StubHub, SeatGeek, AXS, Vivid Seats, Gametime, Lysted, CrowdVolt, \
Dice, Fourvenues, and similar platforms) for a ticket resale business's bookkeeping system.

Rules:
- If this email is NOT a ticket purchase, sale, transfer, listing, or refund \
confirmation, set is_ticket_transaction to false and leave every other field null. \
Do not force a classification on marketing emails, event reminders, or unrelated receipts.
- transaction_type is "buy" if the user acquired/paid for tickets, "sell" if the \
user listed, transferred out, or received payout for tickets they sold.
- total_price is the final amount charged/received, including fees, if stated. \
price_per_ticket is total_price / quantity unless the email states it directly.
- For a sale, total_price is the gross sale amount (what the tickets sold for, e.g. \
"Sale Total", or per-ticket price x quantity), never the seller's payout. \
payout_amount is the net amount paid out to the seller after platform fees \
(e.g. "Payout", "Expected payout"). Populate payout_amount only when the email \
explicitly states it — never estimate or calculate it from a fee percentage or \
from other amounts. Leave payout_amount null for purchases.
- Only populate fees if the email itemizes them separately from the total.
- If the email only confirms that tickets were listed for sale (not yet sold), \
set transfer_status to "listed".
- If the email only confirms that a listing was removed, deleted, or taken down \
(tickets no longer for sale, nothing sold), set transfer_status to "delisted". \
Use "cancelled" only for a cancelled order or sale, never for a removed listing.
- Dates: event_date as YYYY-MM-DD. purchase_date is when this transaction/email \
occurred, not the event date.
- If you cannot confidently determine transaction_type or total_price, still fill \
in every other field you can extract, but set needs_review to true and give a \
one-phrase review_reason. Do not guess a value for total_price or transaction_type \
just to avoid the review flag — an accurate "uncertain" beats a confident wrong guess.
- Same rule for quantity: if the email doesn't clearly state how many tickets were \
involved, leave quantity null, set needs_review to true, and give a review_reason \
explaining that quantity wasn't stated. Do not default quantity to 1 (or any other \
number) just to avoid the review flag.
- Ignore promotional content, unrelated upsells, and boilerplate footers.

Email metadata:
From: {sender}
Subject: {subject}
Received: {received_date}

Email body:
{email_body}
"""