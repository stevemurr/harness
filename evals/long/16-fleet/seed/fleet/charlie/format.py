"""Records as text, for a person."""

from collections import Counter

from .models import Ticket


def render(record: Ticket) -> str:
    """`ref priority [areas] summary`: areas joined by `|`, summary only if there is one."""
    areas = "|".join(record.areas)
    head = f"{record.ref} {record.priority} [{areas}]"
    return f"{head} {record.summary}" if record.summary else head


def top_tag(records: list[Ticket]) -> str:
    """The most frequent areas entry, ties broken alphabetically. Empty if there are none."""
    counts = Counter(tag for record in records for tag in record.areas)
    if not counts:
        return ""
    best = max(sorted(counts), key=lambda tag: counts[tag])
    return best


def summarize(records: list[Ticket]) -> str:
    """`N tickets, priority T, top areas X`. Does not reorder or change its input."""
    count = len(records)
    total = sum(record.priority for record in records)
    top = top_tag(records)
    tail = f", top {top}" if top else ""
    return f"{count} tickets, priority {total}{tail}"
