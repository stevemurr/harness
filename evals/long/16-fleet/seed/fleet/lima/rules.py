"""What a Grant must satisfy once it has been read."""

from .models import Grant

LOWEST = 1
HIGHEST = 60
LONGEST_ABSTRACT = 120
ALLOWED = frozenset(('arts', 'climate', 'data', 'health'))


def check(record: Grant) -> list[str]:
    """Every rule the record breaks, in the order the rules are written here."""
    errors: list[str] = []
    if not LOWEST <= record.months <= HIGHEST:
        errors.append(
            f"{record.award}: months {record.months} "
            + f"is outside {LOWEST}-{HIGHEST}"
        )
    for tag in record.themes:
        if tag not in ALLOWED:
            errors.append(f"{record.award}: unknown themes entry {tag!r}")
    if len(record.abstract) > LONGEST_ABSTRACT:
        errors.append(
            f"{record.award}: abstract longer than {LONGEST_ABSTRACT} characters"
        )
    return errors


def unique(records: list[Grant]) -> list[str]:
    """One error per repeated award, naming it once."""
    seen: set[str] = set()
    repeated: list[str] = []
    for record in records:
        if record.award in seen and record.award not in repeated:
            repeated.append(record.award)
        seen.add(record.award)
    return [f"{ident}: repeated award" for ident in repeated]
