"""Lines to records. One record per line: cohort,seats,tracks,blurb.

`tracks` are separated by `|`, and are lowercased and stripped, so ` Fragile ` is `fragile`.
A blank line is skipped, wherever it is. A line that cannot be read is an error naming its
line number; parsing carries on so that every bad line is reported, not only the first.
"""

from .models import Course


class ParseError(ValueError):
    pass


def parse_line(line: str, number: int) -> Course:
    parts = line.split(",", 3)
    if len(parts) < 3:
        raise ParseError(f"line {number}: expected cohort,seats,tracks[,blurb]")
    cohort = parts[0].strip()
    if not cohort or not cohort.isalnum():
        raise ParseError(f"line {number}: cohort must be letters and digits")
    raw_amount = parts[1].strip()
    if not raw_amount.lstrip("-").isdigit():
        raise ParseError(f"line {number}: seats must be a whole number")
    seats = int(raw_amount)
    tracks = tuple(t.strip().lower() for t in parts[2].split("|") if t.strip())
    blurb = parts[3].strip() if len(parts) > 3 else ""
    return Course(cohort=cohort, seats=seats, tracks=tracks, blurb=blurb)


def parse(text: str) -> tuple[list[Course], list[str]]:
    """Every record that could be read, and every error, in line order."""
    records: list[Course] = []
    errors: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(parse_line(line, number))
        except ParseError as exc:
            errors.append(str(exc))
    return records, errors
