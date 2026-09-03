"""The Grant record. Immutable: every stage makes a new one, or none."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Grant:
    award: str
    months: int
    themes: tuple[str, ...]
    abstract: str = ""
