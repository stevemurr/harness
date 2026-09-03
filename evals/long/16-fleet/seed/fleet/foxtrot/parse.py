"""Lines to records. One record per line: barcode,grams,handling,notes.

`handling` are separated by `|`, and are lowercased and stripped, so ` Fragile ` is `fragile`.
A blank line is skipped, wherever it is. A line that cannot be read is an error naming its
line number; parsing carries on so that every bad line is reported, not only the first.
"""

from .models import Parcel


class ParseError(ValueError):
    pass


def parse_line(line: str, number: int) -> Parcel:
    parts = line.split(",", 3)
    if len(parts) < 3:
        raise ParseError(f"line {number}: expected barcode,grams,handling[,notes]")
    barcode = parts[0].strip()
    if not barcode or not barcode.isalnum():
        raise ParseError(f"line {number}: barcode must be letters and digits")
    raw_amount = parts[1].strip()
    if not raw_amount.lstrip("-").isdigit():
        raise ParseError(f"line {number}: grams must be a whole number")
    grams = int(raw_amount)
    handling = tuple(t.strip().lower() for t in parts[2].split("|") if t.strip())
    notes = parts[3].strip() if len(parts) > 3 else ""
    return Parcel(barcode=barcode, grams=grams, handling=handling, notes=notes)


def parse(text: str) -> tuple[list[Parcel], list[str]]:
    """Every record that could be read, and every error, in line order."""
    records: list[Parcel] = []
    errors: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(parse_line(line, number))
        except ParseError as exc:
            errors.append(str(exc))
    return records, errors
