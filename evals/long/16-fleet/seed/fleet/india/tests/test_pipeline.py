from fleet.india.format import render, summarize, top_tag
from fleet.india.models import Order
from fleet.india.parse import parse, parse_line
from fleet.india.pipeline import run
from fleet.india.rules import ALLOWED, HIGHEST, LONGEST_INSTRUCTION, LOWEST, check, unique

A, B = sorted(ALLOWED)[:2]
GOOD = "in7,500, Partner | phone ,fine"


def rec(ident="x1", amount=500, tags=(), note=""):
    return Order(ident, amount, tuple(tags), note)


def test_parse_reads_a_line_and_normalises_tags():
    record = parse_line(GOOD, 1)
    assert record.sku == "in7"
    assert record.units == 500
    assert record.channels == (A, B)
    assert record.instruction == "fine"


def test_a_note_is_optional_and_defaults_to_empty():
    assert parse_line("in7,500,partner", 1).instruction == ""
    assert Order("x1", 500, ()).instruction == ""


def test_a_negative_amount_is_kept_as_negative_for_the_rules_to_see():
    assert parse_line("in7,-3,partner", 1).units == -3


def test_blank_lines_anywhere_are_skipped_and_parsing_continues():
    records, errors = parse("in7,500,partner\n\n   \nin72,500,phone\n")
    assert [r.sku for r in records] == ["in7", "in72"]
    assert errors == []


def test_a_bad_line_is_an_error_and_the_rest_is_still_read():
    records, errors = parse("in7,500,partner\nnope\nin72,500,phone")
    assert len(records) == 2
    assert errors == ["line 2: expected sku,units,channels[,instruction]"]


def test_the_range_is_inclusive_at_both_ends():
    assert check(rec(amount=LOWEST)) == []
    assert check(rec(amount=HIGHEST)) == []
    outside = f"is outside {LOWEST}-{HIGHEST}"
    assert check(rec(amount=HIGHEST + 1)) == [f"x1: units {HIGHEST + 1} {outside}"]
    assert check(rec(amount=LOWEST - 1)) == [f"x1: units {LOWEST - 1} {outside}"]


def test_only_unknown_tags_are_errors():
    assert check(rec(tags=(A, B))) == []
    assert check(rec(tags=(A, "zzz"))) == ["x1: unknown channels entry 'zzz'"]


def test_the_note_may_be_exactly_the_longest_allowed():
    assert check(rec(note="n" * LONGEST_INSTRUCTION)) == []
    assert check(rec(note="n" * (LONGEST_INSTRUCTION + 1))) == [
        f"x1: instruction longer than {LONGEST_INSTRUCTION} characters"
    ]


def test_a_repeated_ident_is_reported_once():
    assert unique([rec("a"), rec("b"), rec("a"), rec("a")]) == ["a: repeated sku"]
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
    assert summarize(records) == f"2 orders, units 5, top {A}"
    assert records == before
    assert summarize([]) == "0 orders, units 0"


def test_the_report_is_summary_then_records_then_sorted_errors():
    text = "in7,500,partner\nzz9,1000,partner|bad\nin7,500,phone"
    errors = sorted([
        f"in7: repeated sku",
        f"zz9: unknown channels entry 'bad'",
        f"zz9: units 1000 is outside {LOWEST}-{HIGHEST}",
    ])
    expected = [
        f"3 orders, units {500 + 1000 + 500}, top {A}",
        f"in7 500 [{A}]",
        f"zz9 1000 [{A}|bad]",
        f"in7 500 [{B}]",
        *errors,
    ]
    assert run(text).splitlines() == expected
