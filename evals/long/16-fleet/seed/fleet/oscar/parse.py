"""Lines to records. One record per line: case,days,kinds,statement.

`kinds` are separated by `|`, and are lowercased and stripped, so ` Fragile ` is `fragile`.
A blank line is skipped, wherever it is. A line that cannot be read is an error naming its
line number; parsing carries on so that every bad line is reported, not only the first.
"""

from .models import Claim


class ParseError(ValueError):
    pass


def parse_line(line: str, number: int) -> Claim:
    parts = line.split(",", 3)
    if len(parts) < 3:
        raise ParseError(f"line {number}: expected case,days,kinds[,statement]")
    case = parts[0].strip()
    if not case or not case.isalnum():
        raise ParseError(f"line {number}: case must be letters and digits")
    raw_amount = parts[1].strip()
    if not raw_amount.lstrip("-").isdigit():
        raise ParseError(f"line {number}: days must be a whole number")
    days = int(raw_amount)
    kinds = tuple(t.strip().lower() for t in parts[2].split("|") if t.strip())
    statement = parts[3].strip() if len(parts) > 3 else ""
    return Claim(case=case, days=days, kinds=kinds, statement=statement)


def parse(text: str) -> tuple[list[Claim], list[str]]:
    """Every record that could be read, and every error, in line order."""
    records: list[Claim] = []
    errors: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line:
            continue
        try:
            records.append(parse_line(line, number))
        except ParseError as exc:
            errors.append(str(exc))
    return records, errors
