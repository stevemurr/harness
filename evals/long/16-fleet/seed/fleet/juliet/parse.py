"""Lines to records. One record per line: lot,output,stages,log.

`stages` are separated by `|`, and are lowercased and stripped, so ` Fragile ` is `fragile`.
A blank line is skipped, wherever it is. A line that cannot be read is an error naming its
line number; parsing carries on so that every bad line is reported, not only the first.
"""

from .models import Batch


class ParseError(ValueError):
    pass


def parse_line(line: str, number: int) -> Batch:
    parts = line.split(",", 3)
    if len(parts) < 3:
        raise ParseError(f"line {number}: expected lot,output,stages[,log]")
    lot = parts[0].strip()
    if not lot or not lot.isalnum():
        raise ParseError(f"line {number}: lot must be letters and digits")
    raw_amount = parts[1].strip()
    if not raw_amount.lstrip("-").isdigit():
        raise ParseError(f"line {number}: output must be a whole number")
    output = int(raw_amount)
    stages = tuple(t.strip().lower() for t in parts[2].split("|") if t.strip())
    log = parts[3].strip() if len(parts) > 3 else ""
    return Batch(lot=lot, output=output, stages=stages, log=log)


def parse(text: str) -> tuple[list[Batch], list[str]]:
    """Every record that could be read, and every error, in line order."""
    records: list[Batch] = []
    errors: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            return records, errors
        try:
            records.append(parse_line(line, number))
        except ParseError as exc:
            errors.append(str(exc))
    return records, errors
