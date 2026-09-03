from fleet.lima.format import render, summarize, top_tag
from fleet.lima.models import Grant
from fleet.lima.parse import parse, parse_line
from fleet.lima.pipeline import run
from fleet.lima.rules import ALLOWED, HIGHEST, LONGEST_ABSTRACT, LOWEST, check, unique

A, B = sorted(ALLOWED)[:2]
GOOD = "li7,30, Arts | climate ,fine"


def rec(ident="x1", amount=30, tags=(), note=""):
    return Grant(ident, amount, tuple(tags), note)


def test_parse_reads_a_line_and_normalises_tags():
    record = parse_line(GOOD, 1)
    assert record.award == "li7"
    assert record.months == 30
    assert record.themes == (A, B)
    assert record.abstract == "fine"


def test_a_note_is_optional_and_defaults_to_empty():
    assert parse_line("li7,30,arts", 1).abstract == ""
    assert Grant("x1", 30, ()).abstract == ""


def test_a_negative_amount_is_kept_as_negative_for_the_rules_to_see():
    assert parse_line("li7,-3,arts", 1).months == -3


def test_blank_lines_anywhere_are_skipped_and_parsing_continues():
    records, errors = parse("li7,30,arts\n\n   \nli72,30,climate\n")
    assert [r.award for r in records] == ["li7", "li72"]
    assert errors == []


def test_a_bad_line_is_an_error_and_the_rest_is_still_read():
    records, errors = parse("li7,30,arts\nnope\nli72,30,climate")
    assert len(records) == 2
    assert errors == ["line 2: expected award,months,themes[,abstract]"]


def test_the_range_is_inclusive_at_both_ends():
    assert check(rec(amount=LOWEST)) == []
    assert check(rec(amount=HIGHEST)) == []
    outside = f"is outside {LOWEST}-{HIGHEST}"
    assert check(rec(amount=HIGHEST + 1)) == [f"x1: months {HIGHEST + 1} {outside}"]
    assert check(rec(amount=LOWEST - 1)) == [f"x1: months {LOWEST - 1} {outside}"]


def test_only_unknown_tags_are_errors():
    assert check(rec(tags=(A, B))) == []
    assert check(rec(tags=(A, "zzz"))) == ["x1: unknown themes entry 'zzz'"]


def test_the_note_may_be_exactly_the_longest_allowed():
    assert check(rec(note="n" * LONGEST_ABSTRACT)) == []
    assert check(rec(note="n" * (LONGEST_ABSTRACT + 1))) == [
        f"x1: abstract longer than {LONGEST_ABSTRACT} characters"
    ]


def test_a_repeated_ident_is_reported_once():
    assert unique([rec("a"), rec("b"), rec("a"), rec("a")]) == ["a: repeated award"]
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
    assert summarize(records) == f"2 grants, months 5, top {A}"
    assert records == before
    assert summarize([]) == "0 grants, months 0"


def test_the_report_is_summary_then_records_then_sorted_errors():
    text = "li7,30,arts\nzz9,61,arts|bad\nli7,30,climate"
    errors = sorted([
        f"li7: repeated award",
        f"zz9: unknown themes entry 'bad'",
        f"zz9: months 61 is outside {LOWEST}-{HIGHEST}",
    ])
    expected = [
        f"3 grants, months {30 + 61 + 30}, top {A}",
        f"li7 30 [{A}]",
        f"zz9 61 [{A}|bad]",
        f"li7 30 [{B}]",
        *errors,
    ]
    assert run(text).splitlines() == expected
