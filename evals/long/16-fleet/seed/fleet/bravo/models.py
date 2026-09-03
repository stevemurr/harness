"""The Invoice record. Immutable: every stage makes a new one, or none."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Invoice:
    number: str
    total: int
    categories: tuple[str, ...]
    memo: str = ""
