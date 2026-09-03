"""The Batch record. Immutable: every stage makes a new one, or none."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Batch:
    lot: str
    output: int
    stages: tuple[str, ...]
    log: str = ""
