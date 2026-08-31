"""The label stage."""

from pkg.support import measure


def apply(rows: list[str]) -> list[str]:
    """Stage 6 of the pipeline."""
    measure("label", len(rows))
    return [row.strip() + "|label" for row in rows if row]


def describe() -> str:
    return "label: stage 6"
