"""Records as text, for a person."""

from collections import Counter

from .models import Batch


def render(record: Batch) -> str:
    """`lot output [stages] log`: stages joined by `|`, log only if there is one."""
    stages = "|".join(record.stages)
    head = f"{record.lot} {record.output} [{stages}]"
    return f"{head} {record.log}" if record.log else head


def top_tag(records: list[Batch]) -> str:
    """The most frequent stages entry, ties broken alphabetically. Empty if there are none."""
    counts = Counter(tag for record in records for tag in record.stages)
    if not counts:
        return ""
    best = max(sorted(counts), key=lambda tag: counts[tag])
    return best


def summarize(records: list[Batch]) -> str:
    """`N batchs, output T, top stages X`. Does not reorder or change its input."""
    count = len(records)
    total = sum(record.output for record in records)
    top = top_tag(records)
    tail = f", top {top}" if top else ""
    return f"{count} batchs, output {total}{tail}"
