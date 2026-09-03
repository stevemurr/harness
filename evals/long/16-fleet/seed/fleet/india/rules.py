"""What a Order must satisfy once it has been read."""

from .models import Order

LOWEST = 1
HIGHEST = 999
LONGEST_INSTRUCTION = 55
ALLOWED = frozenset(('partner', 'phone', 'store', 'web'))


def check(record: Order) -> list[str]:
    """Every rule the record breaks, in the order the rules are written here."""
    errors: list[str] = []
    if not LOWEST <= record.units <= HIGHEST:
        errors.append(
            f"{record.sku}: units {record.units} "
            + f"is outside {LOWEST}-{HIGHEST}"
        )
    for tag in record.channels:
        if tag not in ALLOWED:
            errors.append(f"{record.sku}: unknown channels entry {tag!r}")
    if len(record.instruction) > LONGEST_INSTRUCTION:
        errors.append(
            f"{record.sku}: instruction longer than {LONGEST_INSTRUCTION} characters"
        )
    return errors


def unique(records: list[Order]) -> list[str]:
    """One error per repeated sku, naming it once."""
    seen: set[str] = set()
    repeated: list[str] = []
    for record in records:
        if record.sku in seen and record.sku not in repeated:
            repeated.append(record.sku)
        seen.add(record.sku)
    return [f"{ident}: repeated sku" for ident in repeated]
