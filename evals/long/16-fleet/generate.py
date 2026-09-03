"""Generate the fleet: sixteen independent packages, each with one planted bug.

Run from the rung's folder: `python3 generate.py` writes `seed/`; `python3 generate.py --check`
also proves, package by package, that the correct source passes its tests and the seeded
source fails them. The seed is committed, so a run never depends on this script; the script
is committed so the seed can be regenerated and so a reader can see exactly what each bug
is without diffing sixteen packages by hand.

Every package has the same shape -- models, parse, rules, format, pipeline, tests -- and
different names, thresholds and vocabulary, so a fix in one says nothing about the others.
The bugs are sixteen different kinds, one each, and every test file exercises every kind,
so a package's tests fail on its own bug only.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEED = HERE / "seed" / "fleet"

#: name, entity, ident, amount, tags, note, LO, HI, MAX_NOTE, allowed tags
PACKAGES = [
    (
        "alpha",
        "Shipment",
        "code",
        "weight",
        "labels",
        "remark",
        1,
        500,
        40,
        ("fragile", "bulk", "cold", "express"),
    ),
    (
        "bravo",
        "Invoice",
        "number",
        "total",
        "categories",
        "memo",
        0,
        9000,
        60,
        ("goods", "service", "tax", "credit"),
    ),
    (
        "charlie",
        "Ticket",
        "ref",
        "priority",
        "areas",
        "summary",
        1,
        5,
        80,
        ("billing", "login", "data", "ui"),
    ),
    (
        "delta",
        "Reading",
        "sensor",
        "value",
        "flags",
        "comment",
        -50,
        150,
        30,
        ("calibrated", "raw", "estimated", "stale"),
    ),
    (
        "echo",
        "Booking",
        "locator",
        "guests",
        "extras",
        "request",
        1,
        12,
        50,
        ("breakfast", "parking", "late", "pet"),
    ),
    (
        "foxtrot",
        "Parcel",
        "barcode",
        "grams",
        "handling",
        "notes",
        10,
        30000,
        45,
        ("liquid", "flat", "rigid", "signed"),
    ),
    (
        "golf",
        "Sample",
        "vial",
        "volume",
        "markers",
        "observation",
        1,
        250,
        70,
        ("plasma", "serum", "whole", "frozen"),
    ),
    (
        "hotel",
        "Room",
        "door",
        "nights",
        "amenities",
        "wish",
        1,
        30,
        35,
        ("balcony", "quiet", "high", "twin"),
    ),
    (
        "india",
        "Order",
        "sku",
        "units",
        "channels",
        "instruction",
        1,
        999,
        55,
        ("web", "phone", "store", "partner"),
    ),
    (
        "juliet",
        "Batch",
        "lot",
        "output",
        "stages",
        "log",
        0,
        100,
        65,
        ("mix", "cure", "cut", "pack"),
    ),
    (
        "kilo",
        "Vehicle",
        "plate",
        "mileage",
        "systems",
        "defect",
        0,
        400000,
        90,
        ("brakes", "lights", "tyres", "engine"),
    ),
    (
        "lima",
        "Grant",
        "award",
        "months",
        "themes",
        "abstract",
        1,
        60,
        120,
        ("health", "climate", "arts", "data"),
    ),
    (
        "mike",
        "Employee",
        "badge",
        "level",
        "teams",
        "bio",
        1,
        9,
        75,
        ("platform", "growth", "ops", "design"),
    ),
    (
        "november",
        "Course",
        "cohort",
        "seats",
        "tracks",
        "blurb",
        5,
        300,
        85,
        ("intro", "advanced", "lab", "seminar"),
    ),
    (
        "oscar",
        "Claim",
        "case",
        "days",
        "kinds",
        "statement",
        0,
        365,
        95,
        ("theft", "damage", "loss", "delay"),
    ),
    (
        "papa",
        "Recipe",
        "dish",
        "minutes",
        "diets",
        "tip",
        1,
        480,
        40,
        ("vegan", "gluten", "nut", "dairy"),
    ),
]

BUGS = [
    "range_off_by_one",
    "inverted_tag_check",
    "tags_not_stripped",
    "top_tag_is_min",
    "note_default_dash",
    "render_swapped",
    "note_length_boundary",
    "errors_unsorted",
    "negative_amount_abs",
    "blank_line_returns",
    "tags_joined_with_comma",
    "summarize_sorts_input",
    "tags_not_lowercased",
    "duplicate_seen_late",
    "blank_line_not_stripped",
    "count_off_by_one",
]

MODELS = '''"""The {Entity} record. Immutable: every stage makes a new one, or none."""

from dataclasses import dataclass


@dataclass(frozen=True)
class {Entity}:
    {ident}: str
    {amount}: int
    {tags}: tuple[str, ...]
    {note}: str = ""
'''

PARSE = '''"""Lines to records. One record per line: {ident},{amount},{tags},{note}.

`{tags}` are separated by `|`, and are lowercased and stripped, so ` Fragile ` is `fragile`.
A blank line is skipped, wherever it is. A line that cannot be read is an error naming its
line number; parsing carries on so that every bad line is reported, not only the first.
"""

from .models import {Entity}


class ParseError(ValueError):
    pass


def parse_line(line: str, number: int) -> {Entity}:
    parts = line.split(",", 3)
    if len(parts) < 3:
        raise ParseError(f"line {{number}}: expected {ident},{amount},{tags}[,{note}]")
    {ident} = parts[0].strip()
    if not {ident} or not {ident}.isalnum():
        raise ParseError(f"line {{number}}: {ident} must be letters and digits")
    raw_amount = parts[1].strip()
    if not raw_amount.lstrip("-").isdigit():
        raise ParseError(f"line {{number}}: {amount} must be a whole number")
    {amount} = int(raw_amount)
    {tags} = tuple(t.strip().lower() for t in parts[2].split("|") if t.strip())
    {note} = parts[3].strip() if len(parts) > 3 else ""
    return {Entity}({ident}={ident}, {amount}={amount}, {tags}={tags}, {note}={note})


def parse(text: str) -> tuple[list[{Entity}], list[str]]:
    """Every record that could be read, and every error, in line order."""
    records: list[{Entity}] = []
    errors: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(parse_line(line, number))
        except ParseError as exc:
            errors.append(str(exc))
    return records, errors
'''

RULES = '''"""What a {Entity} must satisfy once it has been read."""

from .models import {Entity}

LOWEST = {LO}
HIGHEST = {HI}
LONGEST_{NOTE_UPPER} = {MAX_NOTE}
ALLOWED = frozenset({ALLOWED})


def check(record: {Entity}) -> list[str]:
    """Every rule the record breaks, in the order the rules are written here."""
    errors: list[str] = []
    if not LOWEST <= record.{amount} <= HIGHEST:
        errors.append(
            f"{{record.{ident}}}: {amount} {{record.{amount}}} "
            + f"is outside {{LOWEST}}-{{HIGHEST}}"
        )
    for tag in record.{tags}:
        if tag not in ALLOWED:
            errors.append(f"{{record.{ident}}}: unknown {tags} entry {{tag!r}}")
    if len(record.{note}) > LONGEST_{NOTE_UPPER}:
        errors.append(
            f"{{record.{ident}}}: {note} longer than {{LONGEST_{NOTE_UPPER}}} characters"
        )
    return errors


def unique(records: list[{Entity}]) -> list[str]:
    """One error per repeated {ident}, naming it once."""
    seen: set[str] = set()
    repeated: list[str] = []
    for record in records:
        if record.{ident} in seen and record.{ident} not in repeated:
            repeated.append(record.{ident})
        seen.add(record.{ident})
    return [f"{{ident}}: repeated {ident}" for ident in repeated]
'''

FORMAT = '''"""Records as text, for a person."""

from collections import Counter

from .models import {Entity}


def render(record: {Entity}) -> str:
    """`{ident} {amount} [{tags}] {note}`: {tags} joined by `|`, {note} only if there is one."""
    {tags} = "|".join(record.{tags})
    head = f"{{record.{ident}}} {{record.{amount}}} [{{{tags}}}]"
    return f"{{head}} {{record.{note}}}" if record.{note} else head


def top_tag(records: list[{Entity}]) -> str:
    """The most frequent {tags} entry, ties broken alphabetically. Empty if there are none."""
    counts = Counter(tag for record in records for tag in record.{tags})
    if not counts:
        return ""
    best = max(sorted(counts), key=lambda tag: counts[tag])
    return best


def summarize(records: list[{Entity}]) -> str:
    """`N {entity_plural}, {amount} T, top {tags} X`. Does not reorder or change its input."""
    count = len(records)
    total = sum(record.{amount} for record in records)
    top = top_tag(records)
    tail = f", top {{top}}" if top else ""
    return f"{{count}} {entity_plural}, {amount} {{total}}{{tail}}"
'''

PIPELINE = '''"""Text in, report out."""

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
    return "\\n".join(lines)
'''

TESTS = """from fleet.{name}.format import render, summarize, top_tag
from fleet.{name}.models import {Entity}
from fleet.{name}.parse import parse, parse_line
from fleet.{name}.pipeline import run
from fleet.{name}.rules import ALLOWED, HIGHEST, LONGEST_{NOTE_UPPER}, LOWEST, check, unique

A, B = sorted(ALLOWED)[:2]
GOOD = "{good_ident},{mid}, {A_title} | {B} ,fine"


def rec(ident="x1", amount={mid}, tags=(), note=""):
    return {Entity}(ident, amount, tuple(tags), note)


def test_parse_reads_a_line_and_normalises_tags():
    record = parse_line(GOOD, 1)
    assert record.{ident} == "{good_ident}"
    assert record.{amount} == {mid}
    assert record.{tags} == (A, B)
    assert record.{note} == "fine"


def test_a_note_is_optional_and_defaults_to_empty():
    assert parse_line("{good_ident},{mid},{A}", 1).{note} == ""
    assert {Entity}("x1", {mid}, ()).{note} == ""


def test_a_negative_amount_is_kept_as_negative_for_the_rules_to_see():
    assert parse_line("{good_ident},-3,{A}", 1).{amount} == -3


def test_blank_lines_anywhere_are_skipped_and_parsing_continues():
    records, errors = parse("{good_ident},{mid},{A}\\n\\n   \\n{good_ident}2,{mid},{B}\\n")
    assert [r.{ident} for r in records] == ["{good_ident}", "{good_ident}2"]
    assert errors == []


def test_a_bad_line_is_an_error_and_the_rest_is_still_read():
    records, errors = parse("{good_ident},{mid},{A}\\nnope\\n{good_ident}2,{mid},{B}")
    assert len(records) == 2
    assert errors == ["line 2: expected {ident},{amount},{tags}[,{note}]"]


def test_the_range_is_inclusive_at_both_ends():
    assert check(rec(amount=LOWEST)) == []
    assert check(rec(amount=HIGHEST)) == []
    outside = f"is outside {{LOWEST}}-{{HIGHEST}}"
    assert check(rec(amount=HIGHEST + 1)) == [f"x1: {amount} {{HIGHEST + 1}} {{outside}}"]
    assert check(rec(amount=LOWEST - 1)) == [f"x1: {amount} {{LOWEST - 1}} {{outside}}"]


def test_only_unknown_tags_are_errors():
    assert check(rec(tags=(A, B))) == []
    assert check(rec(tags=(A, "zzz"))) == ["x1: unknown {tags} entry 'zzz'"]


def test_the_note_may_be_exactly_the_longest_allowed():
    assert check(rec(note="n" * LONGEST_{NOTE_UPPER})) == []
    assert check(rec(note="n" * (LONGEST_{NOTE_UPPER} + 1))) == [
        f"x1: {note} longer than {{LONGEST_{NOTE_UPPER}}} characters"
    ]


def test_a_repeated_ident_is_reported_once():
    assert unique([rec("a"), rec("b"), rec("a"), rec("a")]) == ["a: repeated {ident}"]
    assert unique([rec("a"), rec("b")]) == []


def test_render_puts_ident_amount_tags_then_note():
    assert render(rec("q9", 7, (A, B), "hello")) == f"q9 7 [{{A}}|{{B}}] hello"
    assert render(rec("q9", 7, (), "")) == "q9 7 []"


def test_top_tag_is_the_most_frequent_with_ties_alphabetical():
    assert top_tag([rec(tags=(B, B)), rec(tags=(A,))]) == B
    assert top_tag([rec(tags=(B,)), rec(tags=(A,))]) == A
    assert top_tag([]) == ""


def test_summarize_counts_and_totals_without_touching_its_input():
    records = [rec("b", 2, (A,)), rec("a", 3, (A,))]
    before = list(records)
    assert summarize(records) == f"2 {entity_plural}, {amount} 5, top {{A}}"
    assert records == before
    assert summarize([]) == "0 {entity_plural}, {amount} 0"


def test_the_report_is_summary_then_records_then_sorted_errors():
    text = "{good_ident},{mid},{A}\\nzz9,{over},{A}|bad\\n{good_ident},{mid},{B}"
    errors = sorted([
        f"{good_ident}: repeated {ident}",
        f"zz9: unknown {tags} entry 'bad'",
        f"zz9: {amount} {over} is outside {{LOWEST}}-{{HIGHEST}}",
    ])
    expected = [
        f"3 {entity_plural}, {amount} {{{mid} + {over} + {mid}}}, top {{A}}",
        f"{good_ident} {mid} [{{A}}]",
        f"zz9 {over} [{{A}}|bad]",
        f"{good_ident} {mid} [{{B}}]",
        *errors,
    ]
    assert run(text).splitlines() == expected
"""

README = """# fleet.{name}

{Entity} records: `{ident},{amount},{tags},{note}` per line. See the docstrings; the tests in
`tests/` are the contract.
"""


def _apply(bug: str, files: dict[str, str], p: dict[str, str]) -> dict[str, str]:
    """One planted bug, as a string edit of the correct source. Every edit is asserted to
    change something, so a template drifting away from a bug is caught here."""
    ident, amount, tags, note = p["ident"], p["amount"], p["tags"], p["note"]
    NOTE = p["NOTE_UPPER"]
    edits = {
        "range_off_by_one": (
            "rules.py",
            f"if not LOWEST <= record.{amount} <= HIGHEST:",
            f"if not LOWEST < record.{amount} <= HIGHEST:",
        ),
        "inverted_tag_check": ("rules.py", "if tag not in ALLOWED:", "if tag in ALLOWED:"),
        "tags_not_stripped": (
            "parse.py",
            f'{tags} = tuple(t.strip().lower() for t in parts[2].split("|") if t.strip())',
            f'{tags} = tuple(t.lower() for t in parts[2].split("|") if t.strip())',
        ),
        "top_tag_is_min": (
            "format.py",
            "best = max(sorted(counts), key=lambda tag: counts[tag])",
            "best = min(sorted(counts), key=lambda tag: counts[tag])",
        ),
        "note_default_dash": ("models.py", f'{note}: str = ""', f'{note}: str = "-"'),
        "render_swapped": (
            "format.py",
            f'head = f"{{record.{ident}}} {{record.{amount}}} [{{{tags}}}]"',
            f'head = f"{{record.{amount}}} {{record.{ident}}} [{{{tags}}}]"',
        ),
        "note_length_boundary": (
            "rules.py",
            f"if len(record.{note}) > LONGEST_{NOTE}:",
            f"if len(record.{note}) >= LONGEST_{NOTE}:",
        ),
        "errors_unsorted": (
            "pipeline.py",
            "lines.extend(sorted(errors))",
            "lines.extend(errors)",
        ),
        "negative_amount_abs": (
            "parse.py",
            f"{amount} = int(raw_amount)",
            f"{amount} = abs(int(raw_amount))",
        ),
        "blank_line_returns": (
            "parse.py",
            "        if not line.strip():\n            continue",
            "        if not line.strip():\n            return records, errors",
        ),
        "tags_joined_with_comma": (
            "format.py",
            f'{tags} = "|".join(record.{tags})',
            f'{tags} = ",".join(record.{tags})',
        ),
        "summarize_sorts_input": (
            "format.py",
            f"    count = len(records)\n    total = sum(record.{amount} for record in records)",
            f"    records.sort(key=lambda record: record.{ident})\n    count = len(records)\n"
            + f"    total = sum(record.{amount} for record in records)",
        ),
        "tags_not_lowercased": (
            "parse.py",
            f'{tags} = tuple(t.strip().lower() for t in parts[2].split("|") if t.strip())',
            f'{tags} = tuple(t.strip() for t in parts[2].split("|") if t.strip())',
        ),
        "duplicate_seen_late": (
            "rules.py",
            f"        if record.{ident} in seen and record.{ident} not in repeated:\n"
            + f"            repeated.append(record.{ident})\n        seen.add(record.{ident})",
            f"        seen.add(record.{ident})\n"
            + f"        if record.{ident} in seen and record.{ident} not in repeated:\n"
            + f"            repeated.append(record.{ident})",
        ),
        "blank_line_not_stripped": (
            "parse.py",
            "        if not line.strip():\n            continue",
            "        if not line:\n            continue",
        ),
        "count_off_by_one": (
            "format.py",
            "    count = len(records)",
            "    count = len(records) - 1 if records else 0",
        ),
    }
    filename, old, new = edits[bug]
    assert old in files[filename], (
        f"{p['name']}/{bug}: template no longer contains the text to break"
    )
    changed = dict(files)
    changed[filename] = files[filename].replace(old, new, 1)
    assert changed[filename] != files[filename]
    return changed


def _package(spec, bug: str, correct: bool) -> dict[str, str]:
    name, entity, ident, amount, tags, note, lo, hi, max_note, allowed = spec
    p = {
        "name": name,
        "Entity": entity,
        "entity_plural": entity.lower() + "s",
        "ident": ident,
        "amount": amount,
        "tags": tags,
        "note": note,
        "NOTE_UPPER": note.upper(),
        "LO": str(lo),
        "HI": str(hi),
        "MAX_NOTE": str(max_note),
        "ALLOWED": repr(tuple(sorted(allowed))),
        "good_ident": f"{name[:2]}7",
        "mid": str((lo + hi) // 2),
        "over": str(hi + 1),
        "A": sorted(allowed)[0],
        "B": sorted(allowed)[1],
        "A_title": sorted(allowed)[0].title(),
    }
    files = {
        "__init__.py": "",
        "models.py": MODELS.format(**p),
        "parse.py": PARSE.format(**p),
        "rules.py": RULES.format(**p),
        "format.py": FORMAT.format(**p),
        "pipeline.py": PIPELINE.format(**p),
        "tests/__init__.py": "",
        "tests/test_pipeline.py": TESTS.format(**p),
        "README.md": README.format(**p),
    }
    return files if correct else _apply(bug, files, p)


def write(root: Path, correct: bool) -> None:
    if root.exists():
        shutil.rmtree(root)
    (root / "fleet").mkdir(parents=True)
    (root / "fleet" / "__init__.py").write_text("")
    for spec, bug in zip(PACKAGES, BUGS, strict=True):
        for relative, text in _package(spec, bug, correct).items():
            path = root / "fleet" / spec[0] / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)


def check() -> int:
    """The correct fleet passes everywhere; the seeded fleet fails in every package."""
    scratch = HERE / ".check"
    write(scratch / "correct", correct=True)
    write(scratch / "seeded", correct=False)
    bad = 0
    for spec, bug in zip(PACKAGES, BUGS, strict=True):
        name = spec[0]
        ok = (
            subprocess.run(
                [sys.executable, "-m", "pytest", "-q", f"fleet/{name}"],
                cwd=scratch / "correct",
                capture_output=True,
                text=True,
            ).returncode
            == 0
        )
        fails = (
            subprocess.run(
                [sys.executable, "-m", "pytest", "-q", f"fleet/{name}"],
                cwd=scratch / "seeded",
                capture_output=True,
                text=True,
            ).returncode
            != 0
        )
        mark = "ok " if ok and fails else "BAD"
        if mark == "BAD":
            bad += 1
        print(f"{mark} {name:<9} {bug:<24} correct passes={ok} seeded fails={fails}")
    shutil.rmtree(scratch)
    return bad


if __name__ == "__main__":
    write(HERE / "seed", correct=False)
    print(f"wrote {SEED}")
    if "--check" in sys.argv:
        raise SystemExit(check())
