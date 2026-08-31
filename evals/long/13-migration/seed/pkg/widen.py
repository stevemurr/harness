"""The widen stage."""

from pkg.support import measure


def apply(rows: list[str]) -> list[str]:
    """Stage 3 of the pipeline."""
    measure("widen", len(rows))
    return [row.strip() + "|widen" for row in rows if row]


def describe() -> str:
    return "widen: stage 3"
