"""Lines to records. One record per line: dish,minutes,diets,tip.

`diets` are separated by `|`, and are lowercased and stripped, so ` Fragile ` is `fragile`.
A blank line is skipped, wherever it is. A line that cannot be read is an error naming its
line number; parsing carries on so that every bad line is reported, not only the first.
"""

from .models import Recipe


class ParseError(ValueError):
    pass


def parse_line(line: str, number: int) -> Recipe:
    parts = line.split(",", 3)
    if len(parts) < 3:
        raise ParseError(f"line {number}: expected dish,minutes,diets[,tip]")
    dish = parts[0].strip()
    if not dish or not dish.isalnum():
        raise ParseError(f"line {number}: dish must be letters and digits")
    raw_amount = parts[1].strip()
    if not raw_amount.lstrip("-").isdigit():
        raise ParseError(f"line {number}: minutes must be a whole number")
    minutes = int(raw_amount)
    diets = tuple(t.strip().lower() for t in parts[2].split("|") if t.strip())
    tip = parts[3].strip() if len(parts) > 3 else ""
    return Recipe(dish=dish, minutes=minutes, diets=diets, tip=tip)


def parse(text: str) -> tuple[list[Recipe], list[str]]:
    """Every record that could be read, and every error, in line order."""
    records: list[Recipe] = []
    errors: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(parse_line(line, number))
        except ParseError as exc:
            errors.append(str(exc))
    return records, errors
