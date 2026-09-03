"""Lines to records. One record per line: sku,units,channels,instruction.

`channels` are separated by `|`, and are lowercased and stripped, so ` Fragile ` is `fragile`.
A blank line is skipped, wherever it is. A line that cannot be read is an error naming its
line number; parsing carries on so that every bad line is reported, not only the first.
"""

from .models import Order


class ParseError(ValueError):
    pass


def parse_line(line: str, number: int) -> Order:
    parts = line.split(",", 3)
    if len(parts) < 3:
        raise ParseError(f"line {number}: expected sku,units,channels[,instruction]")
    sku = parts[0].strip()
    if not sku or not sku.isalnum():
        raise ParseError(f"line {number}: sku must be letters and digits")
    raw_amount = parts[1].strip()
    if not raw_amount.lstrip("-").isdigit():
        raise ParseError(f"line {number}: units must be a whole number")
    units = abs(int(raw_amount))
    channels = tuple(t.strip().lower() for t in parts[2].split("|") if t.strip())
    instruction = parts[3].strip() if len(parts) > 3 else ""
    return Order(sku=sku, units=units, channels=channels, instruction=instruction)


def parse(text: str) -> tuple[list[Order], list[str]]:
    """Every record that could be read, and every error, in line order."""
    records: list[Order] = []
    errors: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(parse_line(line, number))
        except ParseError as exc:
            errors.append(str(exc))
    return records, errors
