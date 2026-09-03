"""Records as text, for a person."""

from collections import Counter

from .models import Invoice


def render(record: Invoice) -> str:
    """`number total [categories] memo`: categories joined by `|`, memo only if there is one."""
    categories = "|".join(record.categories)
    head = f"{record.number} {record.total} [{categories}]"
    return f"{head} {record.memo}" if record.memo else head


def top_tag(records: list[Invoice]) -> str:
    """The most frequent categories entry, ties broken alphabetically. Empty if there are none."""
    counts = Counter(tag for record in records for tag in record.categories)
    if not counts:
        return ""
    best = max(sorted(counts), key=lambda tag: counts[tag])
    return best


def summarize(records: list[Invoice]) -> str:
    """`N invoices, total T, top categories X`. Does not reorder or change its input."""
    count = len(records)
    total = sum(record.total for record in records)
    top = top_tag(records)
    tail = f", top {top}" if top else ""
    return f"{count} invoices, total {total}{tail}"
