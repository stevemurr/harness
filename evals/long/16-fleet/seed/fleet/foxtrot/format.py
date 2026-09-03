"""Records as text, for a person."""

from collections import Counter

from .models import Parcel


def render(record: Parcel) -> str:
    """`barcode grams [handling] notes`: handling joined by `|`, notes only if there is one."""
    handling = "|".join(record.handling)
    head = f"{record.grams} {record.barcode} [{handling}]"
    return f"{head} {record.notes}" if record.notes else head


def top_tag(records: list[Parcel]) -> str:
    """The most frequent handling entry, ties broken alphabetically. Empty if there are none."""
    counts = Counter(tag for record in records for tag in record.handling)
    if not counts:
        return ""
    best = max(sorted(counts), key=lambda tag: counts[tag])
    return best


def summarize(records: list[Parcel]) -> str:
    """`N parcels, grams T, top handling X`. Does not reorder or change its input."""
    count = len(records)
    total = sum(record.grams for record in records)
    top = top_tag(records)
    tail = f", top {top}" if top else ""
    return f"{count} parcels, grams {total}{tail}"
