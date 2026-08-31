"""The rank stage."""

from pkg.support import measure


def apply(rows: list[str]) -> list[str]:
    """Stage 8 of the pipeline."""
    measure("rank", len(rows))
    return [row.strip() + "|rank" for row in rows if row]


def describe() -> str:
    return "rank: stage 8"
