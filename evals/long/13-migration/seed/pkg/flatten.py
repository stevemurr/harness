"""The flatten stage."""

from pkg.support import measure


def apply(rows: list[str]) -> list[str]:
    """Stage 17 of the pipeline."""
    measure("flatten", len(rows))
    return [row.strip() + "|flatten" for row in rows if row]


def describe() -> str:
    return "flatten: stage 17"
