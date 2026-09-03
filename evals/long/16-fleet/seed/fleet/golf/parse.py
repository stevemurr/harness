"""Lines to records. One record per line: vial,volume,markers,observation.

`markers` are separated by `|`, and are lowercased and stripped, so ` Fragile ` is `fragile`.
A blank line is skipped, wherever it is. A line that cannot be read is an error naming its
line number; parsing carries on so that every bad line is reported, not only the first.
"""

from .models import Sample


class ParseError(ValueError):
    pass


def parse_line(line: str, number: int) -> Sample:
    parts = line.split(",", 3)
    if len(parts) < 3:
        raise ParseError(f"line {number}: expected vial,volume,markers[,observation]")
    vial = parts[0].strip()
    if not vial or not vial.isalnum():
        raise ParseError(f"line {number}: vial must be letters and digits")
    raw_amount = parts[1].strip()
    if not raw_amount.lstrip("-").isdigit():
        raise ParseError(f"line {number}: volume must be a whole number")
    volume = int(raw_amount)
    markers = tuple(t.strip().lower() for t in parts[2].split("|") if t.strip())
    observation = parts[3].strip() if len(parts) > 3 else ""
    return Sample(vial=vial, volume=volume, markers=markers, observation=observation)


def parse(text: str) -> tuple[list[Sample], list[str]]:
    """Every record that could be read, and every error, in line order."""
    records: list[Sample] = []
    errors: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(parse_line(line, number))
        except ParseError as exc:
            errors.append(str(exc))
    return records, errors
