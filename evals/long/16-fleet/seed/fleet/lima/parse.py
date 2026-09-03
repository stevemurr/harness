"""Lines to records. One record per line: award,months,themes,abstract.

`themes` are separated by `|`, and are lowercased and stripped, so ` Fragile ` is `fragile`.
A blank line is skipped, wherever it is. A line that cannot be read is an error naming its
line number; parsing carries on so that every bad line is reported, not only the first.
"""

from .models import Grant


class ParseError(ValueError):
    pass


def parse_line(line: str, number: int) -> Grant:
    parts = line.split(",", 3)
    if len(parts) < 3:
        raise ParseError(f"line {number}: expected award,months,themes[,abstract]")
    award = parts[0].strip()
    if not award or not award.isalnum():
        raise ParseError(f"line {number}: award must be letters and digits")
    raw_amount = parts[1].strip()
    if not raw_amount.lstrip("-").isdigit():
        raise ParseError(f"line {number}: months must be a whole number")
    months = int(raw_amount)
    themes = tuple(t.strip().lower() for t in parts[2].split("|") if t.strip())
    abstract = parts[3].strip() if len(parts) > 3 else ""
    return Grant(award=award, months=months, themes=themes, abstract=abstract)


def parse(text: str) -> tuple[list[Grant], list[str]]:
    """Every record that could be read, and every error, in line order."""
    records: list[Grant] = []
    errors: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(parse_line(line, number))
        except ParseError as exc:
            errors.append(str(exc))
    return records, errors
