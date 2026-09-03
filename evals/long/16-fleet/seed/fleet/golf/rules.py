"""What a Sample must satisfy once it has been read."""

from .models import Sample

LOWEST = 1
HIGHEST = 250
LONGEST_OBSERVATION = 70
ALLOWED = frozenset(('frozen', 'plasma', 'serum', 'whole'))


def check(record: Sample) -> list[str]:
    """Every rule the record breaks, in the order the rules are written here."""
    errors: list[str] = []
    if not LOWEST <= record.volume <= HIGHEST:
        errors.append(
            f"{record.vial}: volume {record.volume} "
            + f"is outside {LOWEST}-{HIGHEST}"
        )
    for tag in record.markers:
        if tag not in ALLOWED:
            errors.append(f"{record.vial}: unknown markers entry {tag!r}")
    if len(record.observation) >= LONGEST_OBSERVATION:
        errors.append(
            f"{record.vial}: observation longer than {LONGEST_OBSERVATION} characters"
        )
    return errors


def unique(records: list[Sample]) -> list[str]:
    """One error per repeated vial, naming it once."""
    seen: set[str] = set()
    repeated: list[str] = []
    for record in records:
        if record.vial in seen and record.vial not in repeated:
            repeated.append(record.vial)
        seen.add(record.vial)
    return [f"{ident}: repeated vial" for ident in repeated]
