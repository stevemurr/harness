"""Lines to records. One record per line: number,total,categories,memo.

`categories` are separated by `|`, and are lowercased and stripped, so ` Fragile ` is `fragile`.
A blank line is skipped, wherever it is. A line that cannot be read is an error naming its
line number; parsing carries on so that every bad line is reported, not only the first.
"""

from .models import Invoice


class ParseError(ValueError):
    pass


def parse_line(line: str, number: int) -> Invoice:
    parts = line.split(",", 3)
    if len(parts) < 3:
        raise ParseError(f"line {number}: expected number,total,categories[,memo]")
    number = parts[0].strip()
    if not number or not number.isalnum():
        raise ParseError(f"line {number}: number must be letters and digits")
    raw_amount = parts[1].strip()
    if not raw_amount.lstrip("-").isdigit():
        raise ParseError(f"line {number}: total must be a whole number")
    total = int(raw_amount)
    categories = tuple(t.strip().lower() for t in parts[2].split("|") if t.strip())
    memo = parts[3].strip() if len(parts) > 3 else ""
    return Invoice(number=number, total=total, categories=categories, memo=memo)


def parse(text: str) -> tuple[list[Invoice], list[str]]:
    """Every record that could be read, and every error, in line order."""
    records: list[Invoice] = []
    errors: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(parse_line(line, number))
        except ParseError as exc:
            errors.append(str(exc))
    return records, errors
