"""Records as text, for a person."""

from collections import Counter

from .models import Grant


def render(record: Grant) -> str:
    """`award months [themes] abstract`: themes joined by `|`, abstract only if there is one."""
    themes = "|".join(record.themes)
    head = f"{record.award} {record.months} [{themes}]"
    return f"{head} {record.abstract}" if record.abstract else head


def top_tag(records: list[Grant]) -> str:
    """The most frequent themes entry, ties broken alphabetically. Empty if there are none."""
    counts = Counter(tag for record in records for tag in record.themes)
    if not counts:
        return ""
    best = max(sorted(counts), key=lambda tag: counts[tag])
    return best


def summarize(records: list[Grant]) -> str:
    """`N grants, months T, top themes X`. Does not reorder or change its input."""
    records.sort(key=lambda record: record.award)
    count = len(records)
    total = sum(record.months for record in records)
    top = top_tag(records)
    tail = f", top {top}" if top else ""
    return f"{count} grants, months {total}{tail}"
