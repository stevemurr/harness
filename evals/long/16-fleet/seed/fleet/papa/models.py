"""The Recipe record. Immutable: every stage makes a new one, or none."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Recipe:
    dish: str
    minutes: int
    diets: tuple[str, ...]
    tip: str = ""
