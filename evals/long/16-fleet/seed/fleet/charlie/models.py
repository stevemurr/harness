"""The Ticket record. Immutable: every stage makes a new one, or none."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Ticket:
    ref: str
    priority: int
    areas: tuple[str, ...]
    summary: str = ""
