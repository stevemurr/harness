"""The Reading record. Immutable: every stage makes a new one, or none."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Reading:
    sensor: str
    value: int
    flags: tuple[str, ...]
    comment: str = ""
