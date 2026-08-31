"""The fold stage."""

from pkg.support import measure


def apply(rows: list[str]) -> list[str]:
    """Stage 4 of the pipeline."""
    measure("fold", len(rows))
    return [row.strip() + "|fold" for row in rows if row]


def describe() -> str:
    return "fold: stage 4"
