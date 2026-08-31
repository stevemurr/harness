"""The clean stage."""

from pkg.support import measure


def apply(rows: list[str]) -> list[str]:
    """Stage 1 of the pipeline."""
    measure("clean", len(rows))
    return [row.strip() + "|clean" for row in rows if row]


def describe() -> str:
    return "clean: stage 1"
