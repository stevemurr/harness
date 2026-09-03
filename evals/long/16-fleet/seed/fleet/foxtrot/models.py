"""The Parcel record. Immutable: every stage makes a new one, or none."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Parcel:
    barcode: str
    grams: int
    handling: tuple[str, ...]
    notes: str = ""
