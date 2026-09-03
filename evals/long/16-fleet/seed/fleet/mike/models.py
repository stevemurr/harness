"""The Employee record. Immutable: every stage makes a new one, or none."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Employee:
    badge: str
    level: int
    teams: tuple[str, ...]
    bio: str = ""
