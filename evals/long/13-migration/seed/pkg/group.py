"""The group stage."""

from pkg.support import measure


def apply(rows: list[str]) -> list[str]:
    """Stage 16 of the pipeline."""
    measure("group", len(rows))
    return [row.strip() + "|group" for row in rows if row]


def describe() -> str:
    return "group: stage 16"
