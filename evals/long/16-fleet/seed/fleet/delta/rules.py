"""What a Reading must satisfy once it has been read."""

from .models import Reading

LOWEST = -50
HIGHEST = 150
LONGEST_COMMENT = 30
ALLOWED = frozenset(('calibrated', 'estimated', 'raw', 'stale'))


def check(record: Reading) -> list[str]:
    """Every rule the record breaks, in the order the rules are written here."""
    errors: list[str] = []
    if not LOWEST <= record.value <= HIGHEST:
        errors.append(
            f"{record.sensor}: value {record.value} "
            + f"is outside {LOWEST}-{HIGHEST}"
        )
    for tag in record.flags:
        if tag not in ALLOWED:
            errors.append(f"{record.sensor}: unknown flags entry {tag!r}")
    if len(record.comment) > LONGEST_COMMENT:
        errors.append(
            f"{record.sensor}: comment longer than {LONGEST_COMMENT} characters"
        )
    return errors


def unique(records: list[Reading]) -> list[str]:
    """One error per repeated sensor, naming it once."""
    seen: set[str] = set()
    repeated: list[str] = []
    for record in records:
        if record.sensor in seen and record.sensor not in repeated:
            repeated.append(record.sensor)
        seen.add(record.sensor)
    return [f"{ident}: repeated sensor" for ident in repeated]
