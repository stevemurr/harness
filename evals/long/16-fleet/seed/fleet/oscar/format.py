"""Records as text, for a person."""

from collections import Counter

from .models import Claim


def render(record: Claim) -> str:
    """`case days [kinds] statement`: kinds joined by `|`, statement only if there is one."""
    kinds = "|".join(record.kinds)
    head = f"{record.case} {record.days} [{kinds}]"
    return f"{head} {record.statement}" if record.statement else head


def top_tag(records: list[Claim]) -> str:
    """The most frequent kinds entry, ties broken alphabetically. Empty if there are none."""
    counts = Counter(tag for record in records for tag in record.kinds)
    if not counts:
        return ""
    best = max(sorted(counts), key=lambda tag: counts[tag])
    return best


def summarize(records: list[Claim]) -> str:
    """`N claims, days T, top kinds X`. Does not reorder or change its input."""
    count = len(records)
    total = sum(record.days for record in records)
    top = top_tag(records)
    tail = f", top {top}" if top else ""
    return f"{count} claims, days {total}{tail}"
