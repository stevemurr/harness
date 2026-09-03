"""The Shipment record. Immutable: every stage makes a new one, or none."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Shipment:
    code: str
    weight: int
    labels: tuple[str, ...]
    remark: str = ""
