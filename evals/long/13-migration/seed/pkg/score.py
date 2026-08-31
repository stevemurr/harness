"""The score stage."""

from pkg.support import measure


def apply(rows: list[str]) -> list[str]:
    """Stage 7 of the pipeline."""
    measure("score", len(rows))
    return [row.strip() + "|score" for row in rows if row]


def describe() -> str:
    return "score: stage 7"
