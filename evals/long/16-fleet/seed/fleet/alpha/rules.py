"""What a Shipment must satisfy once it has been read."""

from .models import Shipment

LOWEST = 1
HIGHEST = 500
LONGEST_REMARK = 40
ALLOWED = frozenset(('bulk', 'cold', 'express', 'fragile'))


def check(record: Shipment) -> list[str]:
    """Every rule the record breaks, in the order the rules are written here."""
    errors: list[str] = []
    if not LOWEST < record.weight <= HIGHEST:
        errors.append(
            f"{record.code}: weight {record.weight} "
            + f"is outside {LOWEST}-{HIGHEST}"
        )
    for tag in record.labels:
        if tag not in ALLOWED:
            errors.append(f"{record.code}: unknown labels entry {tag!r}")
    if len(record.remark) > LONGEST_REMARK:
        errors.append(
            f"{record.code}: remark longer than {LONGEST_REMARK} characters"
        )
    return errors


def unique(records: list[Shipment]) -> list[str]:
    """One error per repeated code, naming it once."""
    seen: set[str] = set()
    repeated: list[str] = []
    for record in records:
        if record.code in seen and record.code not in repeated:
            repeated.append(record.code)
        seen.add(record.code)
    return [f"{ident}: repeated code" for ident in repeated]
