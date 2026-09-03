"""What a Booking must satisfy once it has been read."""

from .models import Booking

LOWEST = 1
HIGHEST = 12
LONGEST_REQUEST = 50
ALLOWED = frozenset(('breakfast', 'late', 'parking', 'pet'))


def check(record: Booking) -> list[str]:
    """Every rule the record breaks, in the order the rules are written here."""
    errors: list[str] = []
    if not LOWEST <= record.guests <= HIGHEST:
        errors.append(
            f"{record.locator}: guests {record.guests} "
            + f"is outside {LOWEST}-{HIGHEST}"
        )
    for tag in record.extras:
        if tag not in ALLOWED:
            errors.append(f"{record.locator}: unknown extras entry {tag!r}")
    if len(record.request) > LONGEST_REQUEST:
        errors.append(
            f"{record.locator}: request longer than {LONGEST_REQUEST} characters"
        )
    return errors


def unique(records: list[Booking]) -> list[str]:
    """One error per repeated locator, naming it once."""
    seen: set[str] = set()
    repeated: list[str] = []
    for record in records:
        if record.locator in seen and record.locator not in repeated:
            repeated.append(record.locator)
        seen.add(record.locator)
    return [f"{ident}: repeated locator" for ident in repeated]
