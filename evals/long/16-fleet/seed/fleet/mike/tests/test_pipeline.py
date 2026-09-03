from fleet.mike.format import render, summarize, top_tag
from fleet.mike.models import Employee
from fleet.mike.parse import parse, parse_line
from fleet.mike.pipeline import run
from fleet.mike.rules import ALLOWED, HIGHEST, LONGEST_BIO, LOWEST, check, unique

A, B = sorted(ALLOWED)[:2]
GOOD = "mi7,5, Design | growth ,fine"


def rec(ident="x1", amount=5, tags=(), note=""):
    return Employee(ident, amount, tuple(tags), note)


def test_parse_reads_a_line_and_normalises_tags():
    record = parse_line(GOOD, 1)
    assert record.badge == "mi7"
    assert record.level == 5
    assert record.teams == (A, B)
    assert record.bio == "fine"


def test_a_note_is_optional_and_defaults_to_empty():
    assert parse_line("mi7,5,design", 1).bio == ""
    assert Employee("x1", 5, ()).bio == ""


def test_a_negative_amount_is_kept_as_negative_for_the_rules_to_see():
    assert parse_line("mi7,-3,design", 1).level == -3


def test_blank_lines_anywhere_are_skipped_and_parsing_continues():
    records, errors = parse("mi7,5,design\n\n   \nmi72,5,growth\n")
    assert [r.badge for r in records] == ["mi7", "mi72"]
    assert errors == []


def test_a_bad_line_is_an_error_and_the_rest_is_still_read():
    records, errors = parse("mi7,5,design\nnope\nmi72,5,growth")
    assert len(records) == 2
    assert errors == ["line 2: expected badge,level,teams[,bio]"]


def test_the_range_is_inclusive_at_both_ends():
    assert check(rec(amount=LOWEST)) == []
    assert check(rec(amount=HIGHEST)) == []
    outside = f"is outside {LOWEST}-{HIGHEST}"
    assert check(rec(amount=HIGHEST + 1)) == [f"x1: level {HIGHEST + 1} {outside}"]
    assert check(rec(amount=LOWEST - 1)) == [f"x1: level {LOWEST - 1} {outside}"]


def test_only_unknown_tags_are_errors():
    assert check(rec(tags=(A, B))) == []
    assert check(rec(tags=(A, "zzz"))) == ["x1: unknown teams entry 'zzz'"]


def test_the_note_may_be_exactly_the_longest_allowed():
    assert check(rec(note="n" * LONGEST_BIO)) == []
    assert check(rec(note="n" * (LONGEST_BIO + 1))) == [
        f"x1: bio longer than {LONGEST_BIO} characters"
    ]


def test_a_repeated_ident_is_reported_once():
    assert unique([rec("a"), rec("b"), rec("a"), rec("a")]) == ["a: repeated badge"]
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
    assert summarize(records) == f"2 employees, level 5, top {A}"
    assert records == before
    assert summarize([]) == "0 employees, level 0"


def test_the_report_is_summary_then_records_then_sorted_errors():
    text = "mi7,5,design\nzz9,10,design|bad\nmi7,5,growth"
    errors = sorted([
        f"mi7: repeated badge",
        f"zz9: unknown teams entry 'bad'",
        f"zz9: level 10 is outside {LOWEST}-{HIGHEST}",
    ])
    expected = [
        f"3 employees, level {5 + 10 + 5}, top {A}",
        f"mi7 5 [{A}]",
        f"zz9 10 [{A}|bad]",
        f"mi7 5 [{B}]",
        *errors,
    ]
    assert run(text).splitlines() == expected
