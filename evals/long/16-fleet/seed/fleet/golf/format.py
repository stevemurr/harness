"""Records as text, for a person."""

from collections import Counter

from .models import Sample


def render(record: Sample) -> str:
    """`vial volume [markers] observation`: markers joined by `|`, observation only if there is one."""
    markers = "|".join(record.markers)
    head = f"{record.vial} {record.volume} [{markers}]"
    return f"{head} {record.observation}" if record.observation else head


def top_tag(records: list[Sample]) -> str:
    """The most frequent markers entry, ties broken alphabetically. Empty if there are none."""
    counts = Counter(tag for record in records for tag in record.markers)
    if not counts:
        return ""
    best = max(sorted(counts), key=lambda tag: counts[tag])
    return best


def summarize(records: list[Sample]) -> str:
    """`N samples, volume T, top markers X`. Does not reorder or change its input."""
    count = len(records)
    total = sum(record.volume for record in records)
    top = top_tag(records)
    tail = f", top {top}" if top else ""
    return f"{count} samples, volume {total}{tail}"
