"""Calendar days, from ISO strings."""

from __future__ import annotations

from datetime import date, timedelta


def parse_iso(text: str) -> date:
    """`2026-02-28` as a date. Raises `ValueError` for anything else."""
    return date.fromisoformat(text.strip())


def days_between(start: str, end: str) -> int:
    """Whole days from `start` to `end`: the same day is 0, the next day is 1."""
    first, last = parse_iso(start), parse_iso(end)
    return (last - first).days + 1


def add_days(start: str, days: int) -> str:
    return (parse_iso(start) + timedelta(days=days)).isoformat()


def is_weekend(text: str) -> bool:
    return parse_iso(text).weekday() >= 5
