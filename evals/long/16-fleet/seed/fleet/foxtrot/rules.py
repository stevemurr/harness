"""What a Parcel must satisfy once it has been read."""

from .models import Parcel

LOWEST = 10
HIGHEST = 30000
LONGEST_NOTES = 45
ALLOWED = frozenset(('flat', 'liquid', 'rigid', 'signed'))


def check(record: Parcel) -> list[str]:
    """Every rule the record breaks, in the order the rules are written here."""
    errors: list[str] = []
    if not LOWEST <= record.grams <= HIGHEST:
        errors.append(
            f"{record.barcode}: grams {record.grams} "
            + f"is outside {LOWEST}-{HIGHEST}"
        )
    for tag in record.handling:
        if tag not in ALLOWED:
            errors.append(f"{record.barcode}: unknown handling entry {tag!r}")
    if len(record.notes) > LONGEST_NOTES:
        errors.append(
            f"{record.barcode}: notes longer than {LONGEST_NOTES} characters"
        )
    return errors


def unique(records: list[Parcel]) -> list[str]:
    """One error per repeated barcode, naming it once."""
    seen: set[str] = set()
    repeated: list[str] = []
    for record in records:
        if record.barcode in seen and record.barcode not in repeated:
            repeated.append(record.barcode)
        seen.add(record.barcode)
    return [f"{ident}: repeated barcode" for ident in repeated]
