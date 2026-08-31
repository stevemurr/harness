"""The dedupe stage."""

from pkg.support import measure


def apply(rows: list[str]) -> list[str]:
    """Stage 18 of the pipeline."""
    measure("dedupe", len(rows))
    return [row.strip() + "|dedupe" for row in rows if row]


def describe() -> str:
    return "dedupe: stage 18"
