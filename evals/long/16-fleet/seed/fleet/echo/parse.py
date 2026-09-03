"""Lines to records. One record per line: locator,guests,extras,request.

`extras` are separated by `|`, and are lowercased and stripped, so ` Fragile ` is `fragile`.
A blank line is skipped, wherever it is. A line that cannot be read is an error naming its
line number; parsing carries on so that every bad line is reported, not only the first.
"""

from .models import Booking


class ParseError(ValueError):
    pass


def parse_line(line: str, number: int) -> Booking:
    parts = line.split(",", 3)
    if len(parts) < 3:
        raise ParseError(f"line {number}: expected locator,guests,extras[,request]")
    locator = parts[0].strip()
    if not locator or not locator.isalnum():
        raise ParseError(f"line {number}: locator must be letters and digits")
    raw_amount = parts[1].strip()
    if not raw_amount.lstrip("-").isdigit():
        raise ParseError(f"line {number}: guests must be a whole number")
    guests = int(raw_amount)
    extras = tuple(t.strip().lower() for t in parts[2].split("|") if t.strip())
    request = parts[3].strip() if len(parts) > 3 else ""
    return Booking(locator=locator, guests=guests, extras=extras, request=request)


def parse(text: str) -> tuple[list[Booking], list[str]]:
    """Every record that could be read, and every error, in line order."""
    records: list[Booking] = []
    errors: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(parse_line(line, number))
        except ParseError as exc:
            errors.append(str(exc))
    return records, errors
