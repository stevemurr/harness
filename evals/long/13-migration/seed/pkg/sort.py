"""The sort stage."""

from pkg.support import measure


def apply(rows: list[str]) -> list[str]:
    """Stage 19 of the pipeline."""
    measure("sort", len(rows))
    return [row.strip() + "|sort" for row in rows if row]


def describe() -> str:
    return "sort: stage 19"
