"""The scale stage."""

from pkg.support import measure


def apply(rows: list[str]) -> list[str]:
    """Stage 14 of the pipeline."""
    measure("scale", len(rows))
    return [row.strip() + "|scale" for row in rows if row]


def describe() -> str:
    return "scale: stage 14"
