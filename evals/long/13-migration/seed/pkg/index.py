"""The index stage."""

from pkg.support import measure


def apply(rows: list[str]) -> list[str]:
    """Stage 20 of the pipeline."""
    measure("index", len(rows))
    return [row.strip() + "|index" for row in rows if row]


def describe() -> str:
    return "index: stage 20"
