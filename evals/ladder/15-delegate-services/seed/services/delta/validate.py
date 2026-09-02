"""Calendar dates as YYYY-MM-DD. Leap years are out of scope; February may have 29."""

DAYS = {1: 31, 2: 29, 3: 31, 4: 30, 5: 31, 6: 30, 7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}


def validate(text: str) -> list[str]:
    parts = text.split("-")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return ["date: must be YYYY-MM-DD"]
    year, month, day = (int(p) for p in parts)
    errors = []
    if not 1 <= month <= 12:
        errors.append("month: must be 1-12")
        return errors
    if not 1 <= day <= 30:
        errors.append(f"day: must be 1-{DAYS[month]}")
    if year < 1:
        errors.append("year: must be positive")
    return errors
