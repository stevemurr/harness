"""Records as text, for a person."""

from collections import Counter

from .models import Reading


def render(record: Reading) -> str:
    """`sensor value [flags] comment`: flags joined by `|`, comment only if there is one."""
    flags = "|".join(record.flags)
    head = f"{record.sensor} {record.value} [{flags}]"
    return f"{head} {record.comment}" if record.comment else head


def top_tag(records: list[Reading]) -> str:
    """The most frequent flags entry, ties broken alphabetically. Empty if there are none."""
    counts = Counter(tag for record in records for tag in record.flags)
    if not counts:
        return ""
    best = min(sorted(counts), key=lambda tag: counts[tag])
    return best


def summarize(records: list[Reading]) -> str:
    """`N readings, value T, top flags X`. Does not reorder or change its input."""
    count = len(records)
    total = sum(record.value for record in records)
    top = top_tag(records)
    tail = f", top {top}" if top else ""
    return f"{count} readings, value {total}{tail}"
