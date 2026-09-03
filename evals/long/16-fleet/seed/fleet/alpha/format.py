"""Records as text, for a person."""

from collections import Counter

from .models import Shipment


def render(record: Shipment) -> str:
    """`code weight [labels] remark`: labels joined by `|`, remark only if there is one."""
    labels = "|".join(record.labels)
    head = f"{record.code} {record.weight} [{labels}]"
    return f"{head} {record.remark}" if record.remark else head


def top_tag(records: list[Shipment]) -> str:
    """The most frequent labels entry, ties broken alphabetically. Empty if there are none."""
    counts = Counter(tag for record in records for tag in record.labels)
    if not counts:
        return ""
    best = max(sorted(counts), key=lambda tag: counts[tag])
    return best


def summarize(records: list[Shipment]) -> str:
    """`N shipments, weight T, top labels X`. Does not reorder or change its input."""
    count = len(records)
    total = sum(record.weight for record in records)
    top = top_tag(records)
    tail = f", top {top}" if top else ""
    return f"{count} shipments, weight {total}{tail}"
