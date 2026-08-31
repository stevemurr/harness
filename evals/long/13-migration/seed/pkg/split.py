"""The split stage."""

from pkg.support import measure


def apply(rows: list[str]) -> list[str]:
    """Stage 10 of the pipeline."""
    measure("split", len(rows))
    return [row.strip() + "|split" for row in rows if row]


def describe() -> str:
    return "split: stage 10"
