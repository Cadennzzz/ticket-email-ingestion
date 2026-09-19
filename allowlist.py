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
}


def is_allowlisted(from_address: str) -> bool:
    """Check whether the sender's domain matches or is a subdomain of an allowlisted domain."""
    if not from_address or "@" not in from_address:
        return False

    domain = from_address.rsplit("@", 1)[-1].strip().lower().rstrip(".")

    for allowed in ALLOWLISTED_DOMAINS:
        if domain == allowed or domain.endswith("." + allowed):
            return True
    return False
