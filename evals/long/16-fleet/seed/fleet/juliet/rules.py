"""What a Batch must satisfy once it has been read."""

from .models import Batch

LOWEST = 0
HIGHEST = 100
LONGEST_LOG = 65
ALLOWED = frozenset(('cure', 'cut', 'mix', 'pack'))


def check(record: Batch) -> list[str]:
    """Every rule the record breaks, in the order the rules are written here."""
    errors: list[str] = []
    if not LOWEST <= record.output <= HIGHEST:
        errors.append(
            f"{record.lot}: output {record.output} "
            + f"is outside {LOWEST}-{HIGHEST}"
        )
    for tag in record.stages:
        if tag not in ALLOWED:
            errors.append(f"{record.lot}: unknown stages entry {tag!r}")
    if len(record.log) > LONGEST_LOG:
        errors.append(
            f"{record.lot}: log longer than {LONGEST_LOG} characters"
        )
    return errors


def unique(records: list[Batch]) -> list[str]:
    """One error per repeated lot, naming it once."""
    seen: set[str] = set()
    repeated: list[str] = []
    for record in records:
        if record.lot in seen and record.lot not in repeated:
            repeated.append(record.lot)
        seen.add(record.lot)
    return [f"{ident}: repeated lot" for ident in repeated]
