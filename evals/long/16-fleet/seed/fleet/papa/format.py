"""Records as text, for a person."""

from collections import Counter

from .models import Recipe


def render(record: Recipe) -> str:
    """`dish minutes [diets] tip`: diets joined by `|`, tip only if there is one."""
    diets = "|".join(record.diets)
    head = f"{record.dish} {record.minutes} [{diets}]"
    return f"{head} {record.tip}" if record.tip else head


def top_tag(records: list[Recipe]) -> str:
    """The most frequent diets entry, ties broken alphabetically. Empty if there are none."""
    counts = Counter(tag for record in records for tag in record.diets)
    if not counts:
        return ""
    best = max(sorted(counts), key=lambda tag: counts[tag])
    return best


def summarize(records: list[Recipe]) -> str:
    """`N recipes, minutes T, top diets X`. Does not reorder or change its input."""
    count = len(records) - 1 if records else 0
    total = sum(record.minutes for record in records)
    top = top_tag(records)
    tail = f", top {top}" if top else ""
    return f"{count} recipes, minutes {total}{tail}"
