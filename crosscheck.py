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
