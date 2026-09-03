"""Records as text, for a person."""

from collections import Counter

from .models import Order


def render(record: Order) -> str:
    """`sku units [channels] instruction`: channels joined by `|`, instruction only if there is one."""
    channels = "|".join(record.channels)
    head = f"{record.sku} {record.units} [{channels}]"
    return f"{head} {record.instruction}" if record.instruction else head


def top_tag(records: list[Order]) -> str:
    """The most frequent channels entry, ties broken alphabetically. Empty if there are none."""
    counts = Counter(tag for record in records for tag in record.channels)
    if not counts:
        return ""
    best = max(sorted(counts), key=lambda tag: counts[tag])
    return best


def summarize(records: list[Order]) -> str:
    """`N orders, units T, top channels X`. Does not reorder or change its input."""
    count = len(records)
    total = sum(record.units for record in records)
    top = top_tag(records)
    tail = f", top {top}" if top else ""
    return f"{count} orders, units {total}{tail}"
