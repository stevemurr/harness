"""The trim stage."""

from pkg.support import measure


def apply(rows: list[str]) -> list[str]:
    """Stage 2 of the pipeline."""
    measure("trim", len(rows))
    return [row.strip() + "|trim" for row in rows if row]


def describe() -> str:
    return "trim: stage 2"
