"""Records as text, for a person."""

from collections import Counter

from .models import Vehicle


def render(record: Vehicle) -> str:
    """`plate mileage [systems] defect`: systems joined by `|`, defect only if there is one."""
    systems = ",".join(record.systems)
    head = f"{record.plate} {record.mileage} [{systems}]"
    return f"{head} {record.defect}" if record.defect else head


def top_tag(records: list[Vehicle]) -> str:
    """The most frequent systems entry, ties broken alphabetically. Empty if there are none."""
    counts = Counter(tag for record in records for tag in record.systems)
    if not counts:
        return ""
    best = max(sorted(counts), key=lambda tag: counts[tag])
    return best


def summarize(records: list[Vehicle]) -> str:
    """`N vehicles, mileage T, top systems X`. Does not reorder or change its input."""
    count = len(records)
    total = sum(record.mileage for record in records)
    top = top_tag(records)
    tail = f", top {top}" if top else ""
    return f"{count} vehicles, mileage {total}{tail}"
