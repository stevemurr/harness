"""What a Vehicle must satisfy once it has been read."""

from .models import Vehicle

LOWEST = 0
HIGHEST = 400000
LONGEST_DEFECT = 90
ALLOWED = frozenset(('brakes', 'engine', 'lights', 'tyres'))


def check(record: Vehicle) -> list[str]:
    """Every rule the record breaks, in the order the rules are written here."""
    errors: list[str] = []
    if not LOWEST <= record.mileage <= HIGHEST:
        errors.append(
            f"{record.plate}: mileage {record.mileage} "
            + f"is outside {LOWEST}-{HIGHEST}"
        )
    for tag in record.systems:
        if tag not in ALLOWED:
            errors.append(f"{record.plate}: unknown systems entry {tag!r}")
    if len(record.defect) > LONGEST_DEFECT:
        errors.append(
            f"{record.plate}: defect longer than {LONGEST_DEFECT} characters"
        )
    return errors


def unique(records: list[Vehicle]) -> list[str]:
    """One error per repeated plate, naming it once."""
    seen: set[str] = set()
    repeated: list[str] = []
    for record in records:
        if record.plate in seen and record.plate not in repeated:
            repeated.append(record.plate)
        seen.add(record.plate)
    return [f"{ident}: repeated plate" for ident in repeated]
