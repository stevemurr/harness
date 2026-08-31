"""The merge stage."""

from pkg.support import measure


def apply(rows: list[str]) -> list[str]:
    """Stage 9 of the pipeline."""
    measure("merge", len(rows))
    return [row.strip() + "|merge" for row in rows if row]


def describe() -> str:
    return "merge: stage 9"
