"""The clip stage."""

from pkg.support import measure


def apply(rows: list[str]) -> list[str]:
    """Stage 12 of the pipeline."""
    measure("clip", len(rows))
    return [row.strip() + "|clip" for row in rows if row]


def describe() -> str:
    return "clip: stage 12"
