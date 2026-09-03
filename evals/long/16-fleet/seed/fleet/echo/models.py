"""The Booking record. Immutable: every stage makes a new one, or none."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Booking:
    locator: str
    guests: int
    extras: tuple[str, ...]
    request: str = "-"
