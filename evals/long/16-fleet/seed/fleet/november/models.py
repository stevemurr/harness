"""The Course record. Immutable: every stage makes a new one, or none."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Course:
    cohort: str
    seats: int
    tracks: tuple[str, ...]
    blurb: str = ""
