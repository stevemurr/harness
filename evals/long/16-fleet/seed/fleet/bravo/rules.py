"""What a Invoice must satisfy once it has been read."""

from .models import Invoice

LOWEST = 0
HIGHEST = 9000
LONGEST_MEMO = 60
ALLOWED = frozenset(('credit', 'goods', 'service', 'tax'))


def check(record: Invoice) -> list[str]:
    """Every rule the record breaks, in the order the rules are written here."""
    errors: list[str] = []
    if not LOWEST <= record.total <= HIGHEST:
        errors.append(
            f"{record.number}: total {record.total} "
            + f"is outside {LOWEST}-{HIGHEST}"
        )
    for tag in record.categories:
        if tag in ALLOWED:
            errors.append(f"{record.number}: unknown categories entry {tag!r}")
    if len(record.memo) > LONGEST_MEMO:
        errors.append(
            f"{record.number}: memo longer than {LONGEST_MEMO} characters"
        )
    return errors


def unique(records: list[Invoice]) -> list[str]:
    """One error per repeated number, naming it once."""
    seen: set[str] = set()
    repeated: list[str] = []
    for record in records:
        if record.number in seen and record.number not in repeated:
            repeated.append(record.number)
        seen.add(record.number)
    return [f"{ident}: repeated number" for ident in repeated]
