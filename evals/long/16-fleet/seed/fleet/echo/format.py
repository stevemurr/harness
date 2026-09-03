"""Records as text, for a person."""

from collections import Counter

from .models import Booking


def render(record: Booking) -> str:
    """`locator guests [extras] request`: extras joined by `|`, request only if there is one."""
    extras = "|".join(record.extras)
    head = f"{record.locator} {record.guests} [{extras}]"
    return f"{head} {record.request}" if record.request else head


def top_tag(records: list[Booking]) -> str:
    """The most frequent extras entry, ties broken alphabetically. Empty if there are none."""
    counts = Counter(tag for record in records for tag in record.extras)
    if not counts:
        return ""
    best = max(sorted(counts), key=lambda tag: counts[tag])
    return best


def summarize(records: list[Booking]) -> str:
    """`N bookings, guests T, top extras X`. Does not reorder or change its input."""
    count = len(records)
    total = sum(record.guests for record in records)
    top = top_tag(records)
    tail = f", top {top}" if top else ""
    return f"{count} bookings, guests {total}{tail}"
