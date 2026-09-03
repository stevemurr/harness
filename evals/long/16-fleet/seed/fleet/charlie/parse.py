"""Lines to records. One record per line: ref,priority,areas,summary.

`areas` are separated by `|`, and are lowercased and stripped, so ` Fragile ` is `fragile`.
A blank line is skipped, wherever it is. A line that cannot be read is an error naming its
line number; parsing carries on so that every bad line is reported, not only the first.
"""

from .models import Ticket


class ParseError(ValueError):
    pass


def parse_line(line: str, number: int) -> Ticket:
    parts = line.split(",", 3)
    if len(parts) < 3:
        raise ParseError(f"line {number}: expected ref,priority,areas[,summary]")
    ref = parts[0].strip()
    if not ref or not ref.isalnum():
        raise ParseError(f"line {number}: ref must be letters and digits")
    raw_amount = parts[1].strip()
    if not raw_amount.lstrip("-").isdigit():
        raise ParseError(f"line {number}: priority must be a whole number")
    priority = int(raw_amount)
    areas = tuple(t.lower() for t in parts[2].split("|") if t.strip())
    summary = parts[3].strip() if len(parts) > 3 else ""
    return Ticket(ref=ref, priority=priority, areas=areas, summary=summary)


def parse(text: str) -> tuple[list[Ticket], list[str]]:
    """Every record that could be read, and every error, in line order."""
    records: list[Ticket] = []
    errors: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(parse_line(line, number))
        except ParseError as exc:
            errors.append(str(exc))
    return records, errors
