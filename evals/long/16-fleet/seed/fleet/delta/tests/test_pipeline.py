from fleet.delta.format import render, summarize, top_tag
from fleet.delta.models import Reading
from fleet.delta.parse import parse, parse_line
from fleet.delta.pipeline import run
from fleet.delta.rules import ALLOWED, HIGHEST, LONGEST_COMMENT, LOWEST, check, unique

A, B = sorted(ALLOWED)[:2]
GOOD = "de7,50, Calibrated | estimated ,fine"


def rec(ident="x1", amount=50, tags=(), note=""):
    return Reading(ident, amount, tuple(tags), note)


def test_parse_reads_a_line_and_normalises_tags():
    record = parse_line(GOOD, 1)
    assert record.sensor == "de7"
    assert record.value == 50
    assert record.flags == (A, B)
    assert record.comment == "fine"


def test_a_note_is_optional_and_defaults_to_empty():
    assert parse_line("de7,50,calibrated", 1).comment == ""
    assert Reading("x1", 50, ()).comment == ""


def test_a_negative_amount_is_kept_as_negative_for_the_rules_to_see():
    assert parse_line("de7,-3,calibrated", 1).value == -3


def test_blank_lines_anywhere_are_skipped_and_parsing_continues():
    records, errors = parse("de7,50,calibrated\n\n   \nde72,50,estimated\n")
    assert [r.sensor for r in records] == ["de7", "de72"]
    assert errors == []


def test_a_bad_line_is_an_error_and_the_rest_is_still_read():
    records, errors = parse("de7,50,calibrated\nnope\nde72,50,estimated")
    assert len(records) == 2
    assert errors == ["line 2: expected sensor,value,flags[,comment]"]


def test_the_range_is_inclusive_at_both_ends():
    assert check(rec(amount=LOWEST)) == []
    assert check(rec(amount=HIGHEST)) == []
    outside = f"is outside {LOWEST}-{HIGHEST}"
    assert check(rec(amount=HIGHEST + 1)) == [f"x1: value {HIGHEST + 1} {outside}"]
    assert check(rec(amount=LOWEST - 1)) == [f"x1: value {LOWEST - 1} {outside}"]


def test_only_unknown_tags_are_errors():
    assert check(rec(tags=(A, B))) == []
    assert check(rec(tags=(A, "zzz"))) == ["x1: unknown flags entry 'zzz'"]


def test_the_note_may_be_exactly_the_longest_allowed():
    assert check(rec(note="n" * LONGEST_COMMENT)) == []
    assert check(rec(note="n" * (LONGEST_COMMENT + 1))) == [
        f"x1: comment longer than {LONGEST_COMMENT} characters"
    ]


def test_a_repeated_ident_is_reported_once():
    assert unique([rec("a"), rec("b"), rec("a"), rec("a")]) == ["a: repeated sensor"]
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
    assert summarize(records) == f"2 readings, value 5, top {A}"
    assert records == before
    assert summarize([]) == "0 readings, value 0"


def test_the_report_is_summary_then_records_then_sorted_errors():
    text = "de7,50,calibrated\nzz9,151,calibrated|bad\nde7,50,estimated"
    errors = sorted([
        f"de7: repeated sensor",
        f"zz9: unknown flags entry 'bad'",
        f"zz9: value 151 is outside {LOWEST}-{HIGHEST}",
    ])
    expected = [
        f"3 readings, value {50 + 151 + 50}, top {A}",
        f"de7 50 [{A}]",
        f"zz9 151 [{A}|bad]",
        f"de7 50 [{B}]",
        *errors,
    ]
    assert run(text).splitlines() == expected
