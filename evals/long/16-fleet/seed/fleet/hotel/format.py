"""Records as text, for a person."""

from collections import Counter

from .models import Room


def render(record: Room) -> str:
    """`door nights [amenities] wish`: amenities joined by `|`, wish only if there is one."""
    amenities = "|".join(record.amenities)
    head = f"{record.door} {record.nights} [{amenities}]"
    return f"{head} {record.wish}" if record.wish else head


def top_tag(records: list[Room]) -> str:
    """The most frequent amenities entry, ties broken alphabetically. Empty if there are none."""
    counts = Counter(tag for record in records for tag in record.amenities)
    if not counts:
        return ""
    best = max(sorted(counts), key=lambda tag: counts[tag])
    return best


def summarize(records: list[Room]) -> str:
    """`N rooms, nights T, top amenities X`. Does not reorder or change its input."""
    count = len(records)
    total = sum(record.nights for record in records)
    top = top_tag(records)
    tail = f", top {top}" if top else ""
    return f"{count} rooms, nights {total}{tail}"
