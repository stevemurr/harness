"""Lines to records. One record per line: badge,level,teams,bio.

`teams` are separated by `|`, and are lowercased and stripped, so ` Fragile ` is `fragile`.
A blank line is skipped, wherever it is. A line that cannot be read is an error naming its
line number; parsing carries on so that every bad line is reported, not only the first.
"""

from .models import Employee


class ParseError(ValueError):
    pass


def parse_line(line: str, number: int) -> Employee:
    parts = line.split(",", 3)
    if len(parts) < 3:
        raise ParseError(f"line {number}: expected badge,level,teams[,bio]")
    badge = parts[0].strip()
    if not badge or not badge.isalnum():
        raise ParseError(f"line {number}: badge must be letters and digits")
    raw_amount = parts[1].strip()
    if not raw_amount.lstrip("-").isdigit():
        raise ParseError(f"line {number}: level must be a whole number")
    level = int(raw_amount)
    teams = tuple(t.strip() for t in parts[2].split("|") if t.strip())
    bio = parts[3].strip() if len(parts) > 3 else ""
    return Employee(badge=badge, level=level, teams=teams, bio=bio)


def parse(text: str) -> tuple[list[Employee], list[str]]:
    """Every record that could be read, and every error, in line order."""
    records: list[Employee] = []
    errors: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(parse_line(line, number))
        except ParseError as exc:
            errors.append(str(exc))
    return records, errors
