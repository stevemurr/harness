"""Records as text, for a person."""

from collections import Counter

from .models import Course


def render(record: Course) -> str:
    """`cohort seats [tracks] blurb`: tracks joined by `|`, blurb only if there is one."""
    tracks = "|".join(record.tracks)
    head = f"{record.cohort} {record.seats} [{tracks}]"
    return f"{head} {record.blurb}" if record.blurb else head


def top_tag(records: list[Course]) -> str:
    """The most frequent tracks entry, ties broken alphabetically. Empty if there are none."""
    counts = Counter(tag for record in records for tag in record.tracks)
    if not counts:
        return ""
    best = max(sorted(counts), key=lambda tag: counts[tag])
    return best


def summarize(records: list[Course]) -> str:
    """`N courses, seats T, top tracks X`. Does not reorder or change its input."""
    count = len(records)
    total = sum(record.seats for record in records)
    top = top_tag(records)
    tail = f", top {top}" if top else ""
    return f"{count} courses, seats {total}{tail}"
