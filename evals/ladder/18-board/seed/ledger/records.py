"""Records, and reading them from the ledger file."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path


@dataclass(frozen=True)
class Record:
    date: str
    account: str
    amount: Decimal
    memo: str


def parse_line(line: str) -> Record:
    """One record: `2026-01-03 | groceries | -42.10 | market`."""
    parts = [part.strip() for part in line.split("|")]
    if len(parts) != 4:
        raise ValueError(f"expected 4 fields separated by '|': {line!r}")
    date, account, amount, memo = parts
    try:
        value = Decimal(amount)
    except InvalidOperation as exc:
        raise ValueError(f"not an amount: {amount!r}") from exc
    return Record(date, account, value, memo)


def read_records(path: str | Path) -> list[Record]:
    """Every record in the file, in file order. Blank lines and `#` comments are skipped."""
    lines = Path(path).read_text().splitlines()
    return [parse_line(line) for line in lines if line.strip() and not line.startswith("#")]
