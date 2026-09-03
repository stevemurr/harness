from fleet.oscar.format import render, summarize, top_tag
from fleet.oscar.models import Claim
from fleet.oscar.parse import parse, parse_line
from fleet.oscar.pipeline import run
from fleet.oscar.rules import ALLOWED, HIGHEST, LONGEST_STATEMENT, LOWEST, check, unique

A, B = sorted(ALLOWED)[:2]
GOOD = "os7,182, Damage | delay ,fine"


def rec(ident="x1", amount=182, tags=(), note=""):
    return Claim(ident, amount, tuple(tags), note)


def test_parse_reads_a_line_and_normalises_tags():
    record = parse_line(GOOD, 1)
    assert record.case == "os7"
    assert record.days == 182
    assert record.kinds == (A, B)
    assert record.statement == "fine"


def test_a_note_is_optional_and_defaults_to_empty():
    assert parse_line("os7,182,damage", 1).statement == ""
    assert Claim("x1", 182, ()).statement == ""


def test_a_negative_amount_is_kept_as_negative_for_the_rules_to_see():
    assert parse_line("os7,-3,damage", 1).days == -3


def test_blank_lines_anywhere_are_skipped_and_parsing_continues():
    records, errors = parse("os7,182,damage\n\n   \nos72,182,delay\n")
    assert [r.case for r in records] == ["os7", "os72"]
    assert errors == []


def test_a_bad_line_is_an_error_and_the_rest_is_still_read():
    records, errors = parse("os7,182,damage\nnope\nos72,182,delay")
    assert len(records) == 2
    assert errors == ["line 2: expected case,days,kinds[,statement]"]


def test_the_range_is_inclusive_at_both_ends():
    assert check(rec(amount=LOWEST)) == []
    assert check(rec(amount=HIGHEST)) == []
    outside = f"is outside {LOWEST}-{HIGHEST}"
    assert check(rec(amount=HIGHEST + 1)) == [f"x1: days {HIGHEST + 1} {outside}"]
    assert check(rec(amount=LOWEST - 1)) == [f"x1: days {LOWEST - 1} {outside}"]


def test_only_unknown_tags_are_errors():
    assert check(rec(tags=(A, B))) == []
    assert check(rec(tags=(A, "zzz"))) == ["x1: unknown kinds entry 'zzz'"]


def test_the_note_may_be_exactly_the_longest_allowed():
    assert check(rec(note="n" * LONGEST_STATEMENT)) == []
    assert check(rec(note="n" * (LONGEST_STATEMENT + 1))) == [
        f"x1: statement longer than {LONGEST_STATEMENT} characters"
    ]


def test_a_repeated_ident_is_reported_once():
    assert unique([rec("a"), rec("b"), rec("a"), rec("a")]) == ["a: repeated case"]
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
    assert summarize(records) == f"2 claims, days 5, top {A}"
    assert records == before
    assert summarize([]) == "0 claims, days 0"


def test_the_report_is_summary_then_records_then_sorted_errors():
    text = "os7,182,damage\nzz9,366,damage|bad\nos7,182,delay"
    errors = sorted([
        f"os7: repeated case",
        f"zz9: unknown kinds entry 'bad'",
        f"zz9: days 366 is outside {LOWEST}-{HIGHEST}",
    ])
    expected = [
        f"3 claims, days {182 + 366 + 182}, top {A}",
        f"os7 182 [{A}]",
        f"zz9 366 [{A}|bad]",
        f"os7 182 [{B}]",
        *errors,
    ]
    assert run(text).splitlines() == expected
