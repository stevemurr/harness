"""The filter stage."""

from pkg.support import measure


def apply(rows: list[str]) -> list[str]:
    """Stage 15 of the pipeline."""
    measure("filter", len(rows))
    return [row.strip() + "|filter" for row in rows if row]


def describe() -> str:
    return "filter: stage 15"
