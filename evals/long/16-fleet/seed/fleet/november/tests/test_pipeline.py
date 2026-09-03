from fleet.november.format import render, summarize, top_tag
from fleet.november.models import Course
from fleet.november.parse import parse, parse_line
from fleet.november.pipeline import run
from fleet.november.rules import ALLOWED, HIGHEST, LONGEST_BLURB, LOWEST, check, unique

A, B = sorted(ALLOWED)[:2]
GOOD = "no7,152, Advanced | intro ,fine"


def rec(ident="x1", amount=152, tags=(), note=""):
    return Course(ident, amount, tuple(tags), note)


def test_parse_reads_a_line_and_normalises_tags():
    record = parse_line(GOOD, 1)
    assert record.cohort == "no7"
    assert record.seats == 152
    assert record.tracks == (A, B)
    assert record.blurb == "fine"


def test_a_note_is_optional_and_defaults_to_empty():
    assert parse_line("no7,152,advanced", 1).blurb == ""
    assert Course("x1", 152, ()).blurb == ""


def test_a_negative_amount_is_kept_as_negative_for_the_rules_to_see():
    assert parse_line("no7,-3,advanced", 1).seats == -3


def test_blank_lines_anywhere_are_skipped_and_parsing_continues():
    records, errors = parse("no7,152,advanced\n\n   \nno72,152,intro\n")
    assert [r.cohort for r in records] == ["no7", "no72"]
    assert errors == []


def test_a_bad_line_is_an_error_and_the_rest_is_still_read():
    records, errors = parse("no7,152,advanced\nnope\nno72,152,intro")
    assert len(records) == 2
    assert errors == ["line 2: expected cohort,seats,tracks[,blurb]"]


def test_the_range_is_inclusive_at_both_ends():
    assert check(rec(amount=LOWEST)) == []
    assert check(rec(amount=HIGHEST)) == []
    outside = f"is outside {LOWEST}-{HIGHEST}"
    assert check(rec(amount=HIGHEST + 1)) == [f"x1: seats {HIGHEST + 1} {outside}"]
    assert check(rec(amount=LOWEST - 1)) == [f"x1: seats {LOWEST - 1} {outside}"]


def test_only_unknown_tags_are_errors():
    assert check(rec(tags=(A, B))) == []
    assert check(rec(tags=(A, "zzz"))) == ["x1: unknown tracks entry 'zzz'"]


def test_the_note_may_be_exactly_the_longest_allowed():
    assert check(rec(note="n" * LONGEST_BLURB)) == []
    assert check(rec(note="n" * (LONGEST_BLURB + 1))) == [
        f"x1: blurb longer than {LONGEST_BLURB} characters"
    ]


def test_a_repeated_ident_is_reported_once():
    assert unique([rec("a"), rec("b"), rec("a"), rec("a")]) == ["a: repeated cohort"]
    assert unique([rec("a"), rec("b")]) == []


def test_render_puts_ident_amount_tags_then_note():
    assert render(rec("q9", 7, (A, B), "hello")) == f"q9 7 [{A}|{B}] hello"
    assert render(rec("q9", 7, (), "")) == "q9 7 []"


def test_top_tag_is_the_most_frequent_with_ties_alphabetical():
    assert top_tag([rec(tags=(B, B)), rec(tags=(A,))]) == B
    assert top_tag([rec(tags=(B,)), rec(tags=(A,))]) == A
    assert top_tag([]) == ""


def test_summarize_counts_and_totals_without_touching_its_input():
    records = [rec("b", 2, (A,)), rec("a", 3, (A,))]
    before = list(records)
    assert summarize(records) == f"2 courses, seats 5, top {A}"
    assert records == before
    assert summarize([]) == "0 courses, seats 0"


def test_the_report_is_summary_then_records_then_sorted_errors():
    text = "no7,152,advanced\nzz9,301,advanced|bad\nno7,152,intro"
    errors = sorted([
        f"no7: repeated cohort",
        f"zz9: unknown tracks entry 'bad'",
        f"zz9: seats 301 is outside {LOWEST}-{HIGHEST}",
    ])
    expected = [
        f"3 courses, seats {152 + 301 + 152}, top {A}",
        f"no7 152 [{A}]",
        f"zz9 301 [{A}|bad]",
        f"no7 152 [{B}]",
        *errors,
    ]
    assert run(text).splitlines() == expected
