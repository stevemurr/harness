"""Lines to records. One record per line: sensor,value,flags,comment.

`flags` are separated by `|`, and are lowercased and stripped, so ` Fragile ` is `fragile`.
A blank line is skipped, wherever it is. A line that cannot be read is an error naming its
line number; parsing carries on so that every bad line is reported, not only the first.
"""

from .models import Reading


class ParseError(ValueError):
    pass


def parse_line(line: str, number: int) -> Reading:
    parts = line.split(",", 3)
    if len(parts) < 3:
        raise ParseError(f"line {number}: expected sensor,value,flags[,comment]")
    sensor = parts[0].strip()
    if not sensor or not sensor.isalnum():
        raise ParseError(f"line {number}: sensor must be letters and digits")
    raw_amount = parts[1].strip()
    if not raw_amount.lstrip("-").isdigit():
        raise ParseError(f"line {number}: value must be a whole number")
    value = int(raw_amount)
    flags = tuple(t.strip().lower() for t in parts[2].split("|") if t.strip())
    comment = parts[3].strip() if len(parts) > 3 else ""
    return Reading(sensor=sensor, value=value, flags=flags, comment=comment)


def parse(text: str) -> tuple[list[Reading], list[str]]:
    """Every record that could be read, and every error, in line order."""
    records: list[Reading] = []
    errors: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(parse_line(line, number))
        except ParseError as exc:
            errors.append(str(exc))
    return records, errors
