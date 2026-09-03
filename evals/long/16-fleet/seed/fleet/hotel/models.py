"""The Room record. Immutable: every stage makes a new one, or none."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Room:
    door: str
    nights: int
    amenities: tuple[str, ...]
    wish: str = ""
