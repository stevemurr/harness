"""What a Employee must satisfy once it has been read."""

from .models import Employee

LOWEST = 1
HIGHEST = 9
LONGEST_BIO = 75
ALLOWED = frozenset(('design', 'growth', 'ops', 'platform'))


def check(record: Employee) -> list[str]:
    """Every rule the record breaks, in the order the rules are written here."""
    errors: list[str] = []
    if not LOWEST <= record.level <= HIGHEST:
        errors.append(
            f"{record.badge}: level {record.level} "
            + f"is outside {LOWEST}-{HIGHEST}"
        )
    for tag in record.teams:
        if tag not in ALLOWED:
            errors.append(f"{record.badge}: unknown teams entry {tag!r}")
    if len(record.bio) > LONGEST_BIO:
        errors.append(
            f"{record.badge}: bio longer than {LONGEST_BIO} characters"
        )
    return errors


def unique(records: list[Employee]) -> list[str]:
    """One error per repeated badge, naming it once."""
    seen: set[str] = set()
    repeated: list[str] = []
    for record in records:
        if record.badge in seen and record.badge not in repeated:
            repeated.append(record.badge)
        seen.add(record.badge)
    return [f"{ident}: repeated badge" for ident in repeated]
