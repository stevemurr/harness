"""Lines to records. One record per line: plate,mileage,systems,defect.

`systems` are separated by `|`, and are lowercased and stripped, so ` Fragile ` is `fragile`.
A blank line is skipped, wherever it is. A line that cannot be read is an error naming its
line number; parsing carries on so that every bad line is reported, not only the first.
"""

from .models import Vehicle


class ParseError(ValueError):
    pass


def parse_line(line: str, number: int) -> Vehicle:
    parts = line.split(",", 3)
    if len(parts) < 3:
        raise ParseError(f"line {number}: expected plate,mileage,systems[,defect]")
    plate = parts[0].strip()
    if not plate or not plate.isalnum():
        raise ParseError(f"line {number}: plate must be letters and digits")
    raw_amount = parts[1].strip()
    if not raw_amount.lstrip("-").isdigit():
        raise ParseError(f"line {number}: mileage must be a whole number")
    mileage = int(raw_amount)
    systems = tuple(t.strip().lower() for t in parts[2].split("|") if t.strip())
    defect = parts[3].strip() if len(parts) > 3 else ""
    return Vehicle(plate=plate, mileage=mileage, systems=systems, defect=defect)


def parse(text: str) -> tuple[list[Vehicle], list[str]]:
    """Every record that could be read, and every error, in line order."""
    records: list[Vehicle] = []
    errors: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(parse_line(line, number))
        except ParseError as exc:
            errors.append(str(exc))
    return records, errors
