"""What a Room must satisfy once it has been read."""

from .models import Room

LOWEST = 1
HIGHEST = 30
LONGEST_WISH = 35
ALLOWED = frozenset(('balcony', 'high', 'quiet', 'twin'))


def check(record: Room) -> list[str]:
    """Every rule the record breaks, in the order the rules are written here."""
    errors: list[str] = []
    if not LOWEST <= record.nights <= HIGHEST:
        errors.append(
            f"{record.door}: nights {record.nights} "
            + f"is outside {LOWEST}-{HIGHEST}"
        )
    for tag in record.amenities:
        if tag not in ALLOWED:
            errors.append(f"{record.door}: unknown amenities entry {tag!r}")
    if len(record.wish) > LONGEST_WISH:
        errors.append(
            f"{record.door}: wish longer than {LONGEST_WISH} characters"
        )
    return errors


def unique(records: list[Room]) -> list[str]:
    """One error per repeated door, naming it once."""
    seen: set[str] = set()
    repeated: list[str] = []
    for record in records:
        if record.door in seen and record.door not in repeated:
            repeated.append(record.door)
        seen.add(record.door)
    return [f"{ident}: repeated door" for ident in repeated]
