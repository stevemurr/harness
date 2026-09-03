"""The Claim record. Immutable: every stage makes a new one, or none."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Claim:
    case: str
    days: int
    kinds: tuple[str, ...]
    statement: str = ""
