"""Lines to records. One record per line: door,nights,amenities,wish.

`amenities` are separated by `|`, and are lowercased and stripped, so ` Fragile ` is `fragile`.
A blank line is skipped, wherever it is. A line that cannot be read is an error naming its
line number; parsing carries on so that every bad line is reported, not only the first.
"""

from .models import Room


class ParseError(ValueError):
    pass


def parse_line(line: str, number: int) -> Room:
    parts = line.split(",", 3)
    if len(parts) < 3:
        raise ParseError(f"line {number}: expected door,nights,amenities[,wish]")
    door = parts[0].strip()
    if not door or not door.isalnum():
        raise ParseError(f"line {number}: door must be letters and digits")
    raw_amount = parts[1].strip()
    if not raw_amount.lstrip("-").isdigit():
        raise ParseError(f"line {number}: nights must be a whole number")
    nights = int(raw_amount)
    amenities = tuple(t.strip().lower() for t in parts[2].split("|") if t.strip())
    wish = parts[3].strip() if len(parts) > 3 else ""
    return Room(door=door, nights=nights, amenities=amenities, wish=wish)


def parse(text: str) -> tuple[list[Room], list[str]]:
    """Every record that could be read, and every error, in line order."""
    records: list[Room] = []
    errors: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(parse_line(line, number))
        except ParseError as exc:
            errors.append(str(exc))
    return records, errors
