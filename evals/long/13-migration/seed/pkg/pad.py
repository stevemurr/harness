"""The pad stage."""

from pkg.support import measure


def apply(rows: list[str]) -> list[str]:
    """Stage 11 of the pipeline."""
    measure("pad", len(rows))
    return [row.strip() + "|pad" for row in rows if row]


def describe() -> str:
    return "pad: stage 11"
