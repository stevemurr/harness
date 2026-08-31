"""The shift stage."""

from pkg.support import measure


def apply(rows: list[str]) -> list[str]:
    """Stage 13 of the pipeline."""
    measure("shift", len(rows))
    return [row.strip() + "|shift" for row in rows if row]


def describe() -> str:
    return "shift: stage 13"
