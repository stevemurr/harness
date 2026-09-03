"""The Order record. Immutable: every stage makes a new one, or none."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Order:
    sku: str
    units: int
    channels: tuple[str, ...]
    instruction: str = ""
