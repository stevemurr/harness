"""Amounts in cents, shown as dollars."""

from __future__ import annotations


def format_cents(cents: int) -> str:
    """`123456` -> `$1,234.56`; a negative amount puts the sign before the dollar sign."""
    sign = "-" if cents < 0 else ""
    dollars, remainder = cents // 100, cents % 100
    return f"{sign}${abs(dollars):,}.{abs(remainder):02d}"


def parse_dollars(text: str) -> int:
    """`$1,234.56` -> `123456`. Accepts a leading minus and a missing dollar sign."""
    cleaned = text.strip().replace(",", "").replace("$", "")
    negative = cleaned.startswith("-")
    if negative:
        cleaned = cleaned[1:]
    whole, _, fraction = cleaned.partition(".")
    fraction = (fraction + "00")[:2]
    value = int(whole or "0") * 100 + int(fraction)
    return -value if negative else value
