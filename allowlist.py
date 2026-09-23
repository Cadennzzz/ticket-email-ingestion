"""
Sender-domain allowlist for ticket-marketplace emails.

Only emails from these domains (or a subdomain of one) get sent to the LLM.
This keeps API usage down and avoids wasting calls on phishing/spam that
spoofs a display name but not the domain.

To add a platform: add its root domain below. Subdomains (mail.*, email.*,
etc.) are matched automatically by is_allowlisted, so you only need the
root domain unless the platform sends from something unrelated to it
(e.g. a third-party ESP domain), in which case add that domain directly too.
"""

from typing import Optional

ALLOWLISTED_DOMAINS = {
    "ticketmaster.com",
    "email.ticketmaster.com",
    "mail.ticketmaster.com",
    "stubhub.com",
    "mail.stubhub.com",
    "email.stubhub.com",
    "seatgeek.com",
    "email.seatgeek.com",
    "mail.seatgeek.com",
    "axs.com",
    "email.axs.com",
    "mail.axs.com",
    "vividseats.com",
    "email.vividseats.com",
    "mail.vividseats.com",
    "gametime.co",
    "email.gametime.co",
    "mail.gametime.co",

    # Added 2026-09-19 based on a real sender-domain audit (audit_senders.py)
    # against inbox history across the destination and source mailboxes,
    # plus manually confirmed additions. Unlike the hand-picked list above,
    # these were surfaced from actual traffic rather than known platforms.
    "lysted.com",
    "automatiq.com",
    "dice.fm",
    "mailer.dice.fm",
    "mg.victorylive.com",
    "fourvenues.com",
    "tx.crowdvolt.com",
    "m.atomtickets.com",
    "tickpick.com",
    "seetickets.us",
    "frontgatetickets.com",
    "ticketweb.com",
    "pro.laylo.com",
    "events.aegpresents.com",
    "events.goldenvoice.com",
    "events.bowerypresents.com",
    "email.insomniac.com",
    "h.redrocksonline.com",
    "cubuffs.paclive.com",
    "go.cubuffs.com",
    "stagefronttickets.com",
    "tixr.com",
    "clubtickets.com",
    "megatix.com.au",
    "universe.com",
    "app.cashortrade.org",
    "f2f.bandsintown.com",
    "replyto.livenation.com",
    "email.livenation.com",
    "order.eventbrite.com",
    "event.eventbrite.com",
    "hyperwallet.com",
    "showclix.com",
    "prekindle.com",
    "taogroup.com",
    "tickets-shotgun.live",
    "bellyupaspen.com",
}


# Sender domain -> platform label, matched the same way as the allowlist
# (exact or subdomain; the most specific key wins). Labels follow the
# spelling already used in the Excel workbook where one exists, and must
# stay in sync with the platform Literal in extraction_schema.py.
#
# The sender domain is authoritative for platform — ingest.py overrides
# whatever the LLM picked with this, since the model tends to fall back to
# "Other" for anything outside the big marketplaces.
PLATFORM_BY_DOMAIN = {
    "ticketmaster.com": "Ticketmaster",
    "livenation.com": "Ticketmaster",
    "stubhub.com": "StubHub",
    "seatgeek.com": "SeatGeek",
    "axs.com": "AXS",
    "vividseats.com": "Vivid Seats",
    "gametime.co": "Gametime",
    "lysted.com": "Lysted",
    "automatiq.com": "Lysted",
    "hyperwallet.com": "Lysted",  # Lysted's payout processor
    "crowdvolt.com": "CrowdVolt",
    "dice.fm": "Dice",
    "fourvenues.com": "Fourvenues",
    "paclive.com": "Paciolan",
    "go.cubuffs.com": "Paciolan",
    "bellyupaspen.com": "Belly Up",
    "frontgatetickets.com": "Front Gate Tickets",
    "insomniac.com": "Insomniac",
    "redrocksonline.com": "Red Rocks",
    "ticketweb.com": "TicketWeb",
    "atomtickets.com": "Atom Tickets",
    "tickpick.com": "TickPick",
    "seetickets.us": "See Tickets",
    "showclix.com": "ShowClix",
    "prekindle.com": "Prekindle",
    "taogroup.com": "Tao Group",
    "cashortrade.org": "Cash or Trade",
    "victorylive.com": "Victory Live",
    "tixr.com": "Tixr",
    "stagefronttickets.com": "Stagefront",
    "tickets-shotgun.live": "Shotgun",
    "clubtickets.com": "Club Tickets",
    "megatix.com.au": "Megatix",
    "universe.com": "Universe",
    "eventbrite.com": "Eventbrite",
    "aegpresents.com": "AEG Presents",
    "goldenvoice.com": "Goldenvoice",
    "bowerypresents.com": "Bowery Presents",
    "laylo.com": "Laylo",
    "bandsintown.com": "Bandsintown",
}


def _sender_domain(from_address: str) -> Optional[str]:
    if not from_address or "@" not in from_address:
        return None
    return from_address.rsplit("@", 1)[-1].strip().lower().rstrip(".")


def is_allowlisted(from_address: str) -> bool:
    """Check whether the sender's domain matches or is a subdomain of an allowlisted domain."""
    domain = _sender_domain(from_address)
    if domain is None:
        return False

    for allowed in ALLOWLISTED_DOMAINS:
        if domain == allowed or domain.endswith("." + allowed):
            return True
    return False


def platform_for_sender(from_address: str) -> Optional[str]:
    """Platform label for the sender's domain, or None if it isn't mapped."""
    domain = _sender_domain(from_address)
    if domain is None:
        return None

    matches = [key for key in PLATFORM_BY_DOMAIN if domain == key or domain.endswith("." + key)]
    if not matches:
        return None
    return PLATFORM_BY_DOMAIN[max(matches, key=len)]
