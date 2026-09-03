"""What a Ticket must satisfy once it has been read."""

from .models import Ticket

LOWEST = 1
HIGHEST = 5
LONGEST_SUMMARY = 80
ALLOWED = frozenset(('billing', 'data', 'login', 'ui'))


def check(record: Ticket) -> list[str]:
    """Every rule the record breaks, in the order the rules are written here."""
    errors: list[str] = []
    if not LOWEST <= record.priority <= HIGHEST:
        errors.append(
            f"{record.ref}: priority {record.priority} "
            + f"is outside {LOWEST}-{HIGHEST}"
        )
    for tag in record.areas:
        if tag not in ALLOWED:
            errors.append(f"{record.ref}: unknown areas entry {tag!r}")
    if len(record.summary) > LONGEST_SUMMARY:
        errors.append(
            f"{record.ref}: summary longer than {LONGEST_SUMMARY} characters"
        )
    return errors


def unique(records: list[Ticket]) -> list[str]:
    """One error per repeated ref, naming it once."""
    seen: set[str] = set()
    repeated: list[str] = []
    for record in records:
        if record.ref in seen and record.ref not in repeated:
            repeated.append(record.ref)
        seen.add(record.ref)
    return [f"{ident}: repeated ref" for ident in repeated]
