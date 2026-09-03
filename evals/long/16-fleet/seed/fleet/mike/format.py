"""Records as text, for a person."""

from collections import Counter

from .models import Employee


def render(record: Employee) -> str:
    """`badge level [teams] bio`: teams joined by `|`, bio only if there is one."""
    teams = "|".join(record.teams)
    head = f"{record.badge} {record.level} [{teams}]"
    return f"{head} {record.bio}" if record.bio else head


def top_tag(records: list[Employee]) -> str:
    """The most frequent teams entry, ties broken alphabetically. Empty if there are none."""
    counts = Counter(tag for record in records for tag in record.teams)
    if not counts:
        return ""
    best = max(sorted(counts), key=lambda tag: counts[tag])
    return best


def summarize(records: list[Employee]) -> str:
    """`N employees, level T, top teams X`. Does not reorder or change its input."""
    count = len(records)
    total = sum(record.level for record in records)
    top = top_tag(records)
    tail = f", top {top}" if top else ""
    return f"{count} employees, level {total}{tail}"
