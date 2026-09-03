"""Text in, report out."""

from .format import render, summarize
from .parse import parse
from .rules import check, unique


def run(text: str) -> str:
    """The summary line, then every accepted record rendered in input order, then every
    error sorted, one per line. Records with rule errors are still rendered: the report
    shows what was read and what was wrong with it, and hides nothing."""
    records, errors = parse(text)
    for record in records:
        errors.extend(check(record))
    errors.extend(unique(records))
    lines = [summarize(records)]
    lines.extend(render(record) for record in records)
    lines.extend(sorted(errors))
    return "\n".join(lines)
