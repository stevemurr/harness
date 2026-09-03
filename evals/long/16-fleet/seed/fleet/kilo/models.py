"""The Vehicle record. Immutable: every stage makes a new one, or none."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Vehicle:
    plate: str
    mileage: int
    systems: tuple[str, ...]
    defect: str = ""
