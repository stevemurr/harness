"""The Sample record. Immutable: every stage makes a new one, or none."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Sample:
    vial: str
    volume: int
    markers: tuple[str, ...]
    observation: str = ""
