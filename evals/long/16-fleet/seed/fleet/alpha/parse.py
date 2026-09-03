"""Lines to records. One record per line: code,weight,labels,remark.

`labels` are separated by `|`, and are lowercased and stripped, so ` Fragile ` is `fragile`.
A blank line is skipped, wherever it is. A line that cannot be read is an error naming its
line number; parsing carries on so that every bad line is reported, not only the first.
"""

from .models import Shipment


class ParseError(ValueError):
    pass


def parse_line(line: str, number: int) -> Shipment:
    parts = line.split(",", 3)
    if len(parts) < 3:
        raise ParseError(f"line {number}: expected code,weight,labels[,remark]")
    code = parts[0].strip()
    if not code or not code.isalnum():
        raise ParseError(f"line {number}: code must be letters and digits")
    raw_amount = parts[1].strip()
    if not raw_amount.lstrip("-").isdigit():
        raise ParseError(f"line {number}: weight must be a whole number")
    weight = int(raw_amount)
    labels = tuple(t.strip().lower() for t in parts[2].split("|") if t.strip())
    remark = parts[3].strip() if len(parts) > 3 else ""
    return Shipment(code=code, weight=weight, labels=labels, remark=remark)


def parse(text: str) -> tuple[list[Shipment], list[str]]:
    """Every record that could be read, and every error, in line order."""
    records: list[Shipment] = []
    errors: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(parse_line(line, number))
        except ParseError as exc:
            errors.append(str(exc))
    return records, errors
