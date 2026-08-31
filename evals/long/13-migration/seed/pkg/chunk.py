"""The chunk stage."""

from pkg.support import measure


def apply(rows: list[str]) -> list[str]:
    """Stage 5 of the pipeline."""
    measure("chunk", len(rows))
    return [row.strip() + "|chunk" for row in rows if row]


def describe() -> str:
    return "chunk: stage 5"
