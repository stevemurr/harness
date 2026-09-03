"""What a Claim must satisfy once it has been read."""

from .models import Claim

LOWEST = 0
HIGHEST = 365
LONGEST_STATEMENT = 95
ALLOWED = frozenset(('damage', 'delay', 'loss', 'theft'))


def check(record: Claim) -> list[str]:
    """Every rule the record breaks, in the order the rules are written here."""
    errors: list[str] = []
    if not LOWEST <= record.days <= HIGHEST:
        errors.append(
            f"{record.case}: days {record.days} "
            + f"is outside {LOWEST}-{HIGHEST}"
        )
    for tag in record.kinds:
        if tag not in ALLOWED:
            errors.append(f"{record.case}: unknown kinds entry {tag!r}")
    if len(record.statement) > LONGEST_STATEMENT:
        errors.append(
            f"{record.case}: statement longer than {LONGEST_STATEMENT} characters"
        )
    return errors


def unique(records: list[Claim]) -> list[str]:
    """One error per repeated case, naming it once."""
    seen: set[str] = set()
    repeated: list[str] = []
    for record in records:
        if record.case in seen and record.case not in repeated:
            repeated.append(record.case)
        seen.add(record.case)
    return [f"{ident}: repeated case" for ident in repeated]
