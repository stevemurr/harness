"""What a Course must satisfy once it has been read."""

from .models import Course

LOWEST = 5
HIGHEST = 300
LONGEST_BLURB = 85
ALLOWED = frozenset(('advanced', 'intro', 'lab', 'seminar'))


def check(record: Course) -> list[str]:
    """Every rule the record breaks, in the order the rules are written here."""
    errors: list[str] = []
    if not LOWEST <= record.seats <= HIGHEST:
        errors.append(
            f"{record.cohort}: seats {record.seats} "
            + f"is outside {LOWEST}-{HIGHEST}"
        )
    for tag in record.tracks:
        if tag not in ALLOWED:
            errors.append(f"{record.cohort}: unknown tracks entry {tag!r}")
    if len(record.blurb) > LONGEST_BLURB:
        errors.append(
            f"{record.cohort}: blurb longer than {LONGEST_BLURB} characters"
        )
    return errors


def unique(records: list[Course]) -> list[str]:
    """One error per repeated cohort, naming it once."""
    seen: set[str] = set()
    repeated: list[str] = []
    for record in records:
        seen.add(record.cohort)
        if record.cohort in seen and record.cohort not in repeated:
            repeated.append(record.cohort)
    return [f"{ident}: repeated cohort" for ident in repeated]
