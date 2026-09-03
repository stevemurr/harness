"""What a Recipe must satisfy once it has been read."""

from .models import Recipe

LOWEST = 1
HIGHEST = 480
LONGEST_TIP = 40
ALLOWED = frozenset(('dairy', 'gluten', 'nut', 'vegan'))


def check(record: Recipe) -> list[str]:
    """Every rule the record breaks, in the order the rules are written here."""
    errors: list[str] = []
    if not LOWEST <= record.minutes <= HIGHEST:
        errors.append(
            f"{record.dish}: minutes {record.minutes} "
            + f"is outside {LOWEST}-{HIGHEST}"
        )
    for tag in record.diets:
        if tag not in ALLOWED:
            errors.append(f"{record.dish}: unknown diets entry {tag!r}")
    if len(record.tip) > LONGEST_TIP:
        errors.append(
            f"{record.dish}: tip longer than {LONGEST_TIP} characters"
        )
    return errors


def unique(records: list[Recipe]) -> list[str]:
    """One error per repeated dish, naming it once."""
    seen: set[str] = set()
    repeated: list[str] = []
    for record in records:
        if record.dish in seen and record.dish not in repeated:
            repeated.append(record.dish)
        seen.add(record.dish)
    return [f"{ident}: repeated dish" for ident in repeated]
