from fleet.charlie.format import render, summarize, top_tag
from fleet.charlie.models import Ticket
from fleet.charlie.parse import parse, parse_line
from fleet.charlie.pipeline import run
from fleet.charlie.rules import ALLOWED, HIGHEST, LONGEST_SUMMARY, LOWEST, check, unique

A, B = sorted(ALLOWED)[:2]
GOOD = "ch7,3, Billing | data ,fine"


def rec(ident="x1", amount=3, tags=(), note=""):
    return Ticket(ident, amount, tuple(tags), note)


def test_parse_reads_a_line_and_normalises_tags():
    record = parse_line(GOOD, 1)
    assert record.ref == "ch7"
    assert record.priority == 3
    assert record.areas == (A, B)
    assert record.summary == "fine"


def test_a_note_is_optional_and_defaults_to_empty():
    assert parse_line("ch7,3,billing", 1).summary == ""
    assert Ticket("x1", 3, ()).summary == ""


def test_a_negative_amount_is_kept_as_negative_for_the_rules_to_see():
    assert parse_line("ch7,-3,billing", 1).priority == -3


def test_blank_lines_anywhere_are_skipped_and_parsing_continues():
    records, errors = parse("ch7,3,billing\n\n   \nch72,3,data\n")
    assert [r.ref for r in records] == ["ch7", "ch72"]
    assert errors == []


def test_a_bad_line_is_an_error_and_the_rest_is_still_read():
    records, errors = parse("ch7,3,billing\nnope\nch72,3,data")
    assert len(records) == 2
    assert errors == ["line 2: expected ref,priority,areas[,summary]"]


def test_the_range_is_inclusive_at_both_ends():
    assert check(rec(amount=LOWEST)) == []
    assert check(rec(amount=HIGHEST)) == []
    outside = f"is outside {LOWEST}-{HIGHEST}"
    assert check(rec(amount=HIGHEST + 1)) == [f"x1: priority {HIGHEST + 1} {outside}"]
    assert check(rec(amount=LOWEST - 1)) == [f"x1: priority {LOWEST - 1} {outside}"]


def test_only_unknown_tags_are_errors():
    assert check(rec(tags=(A, B))) == []
    assert check(rec(tags=(A, "zzz"))) == ["x1: unknown areas entry 'zzz'"]


def test_the_note_may_be_exactly_the_longest_allowed():
    assert check(rec(note="n" * LONGEST_SUMMARY)) == []
    assert check(rec(note="n" * (LONGEST_SUMMARY + 1))) == [
        f"x1: summary longer than {LONGEST_SUMMARY} characters"
    ]


def test_a_repeated_ident_is_reported_once():
    assert unique([rec("a"), rec("b"), rec("a"), rec("a")]) == ["a: repeated ref"]
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
    assert summarize(records) == f"2 tickets, priority 5, top {A}"
    assert records == before
    assert summarize([]) == "0 tickets, priority 0"


def test_the_report_is_summary_then_records_then_sorted_errors():
    text = "ch7,3,billing\nzz9,6,billing|bad\nch7,3,data"
    errors = sorted([
        f"ch7: repeated ref",
        f"zz9: unknown areas entry 'bad'",
        f"zz9: priority 6 is outside {LOWEST}-{HIGHEST}",
    ])
    expected = [
        f"3 tickets, priority {3 + 6 + 3}, top {A}",
        f"ch7 3 [{A}]",
        f"zz9 6 [{A}|bad]",
        f"ch7 3 [{B}]",
        *errors,
    ]
    assert run(text).splitlines() == expected
