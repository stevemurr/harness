from fleet.echo.format import render, summarize, top_tag
from fleet.echo.models import Booking
from fleet.echo.parse import parse, parse_line
from fleet.echo.pipeline import run
from fleet.echo.rules import ALLOWED, HIGHEST, LONGEST_REQUEST, LOWEST, check, unique

A, B = sorted(ALLOWED)[:2]
GOOD = "ec7,6, Breakfast | late ,fine"


def rec(ident="x1", amount=6, tags=(), note=""):
    return Booking(ident, amount, tuple(tags), note)


def test_parse_reads_a_line_and_normalises_tags():
    record = parse_line(GOOD, 1)
    assert record.locator == "ec7"
    assert record.guests == 6
    assert record.extras == (A, B)
    assert record.request == "fine"


def test_a_note_is_optional_and_defaults_to_empty():
    assert parse_line("ec7,6,breakfast", 1).request == ""
    assert Booking("x1", 6, ()).request == ""


def test_a_negative_amount_is_kept_as_negative_for_the_rules_to_see():
    assert parse_line("ec7,-3,breakfast", 1).guests == -3


def test_blank_lines_anywhere_are_skipped_and_parsing_continues():
    records, errors = parse("ec7,6,breakfast\n\n   \nec72,6,late\n")
    assert [r.locator for r in records] == ["ec7", "ec72"]
    assert errors == []


def test_a_bad_line_is_an_error_and_the_rest_is_still_read():
    records, errors = parse("ec7,6,breakfast\nnope\nec72,6,late")
    assert len(records) == 2
    assert errors == ["line 2: expected locator,guests,extras[,request]"]


def test_the_range_is_inclusive_at_both_ends():
    assert check(rec(amount=LOWEST)) == []
    assert check(rec(amount=HIGHEST)) == []
    outside = f"is outside {LOWEST}-{HIGHEST}"
    assert check(rec(amount=HIGHEST + 1)) == [f"x1: guests {HIGHEST + 1} {outside}"]
    assert check(rec(amount=LOWEST - 1)) == [f"x1: guests {LOWEST - 1} {outside}"]


def test_only_unknown_tags_are_errors():
    assert check(rec(tags=(A, B))) == []
    assert check(rec(tags=(A, "zzz"))) == ["x1: unknown extras entry 'zzz'"]


def test_the_note_may_be_exactly_the_longest_allowed():
    assert check(rec(note="n" * LONGEST_REQUEST)) == []
    assert check(rec(note="n" * (LONGEST_REQUEST + 1))) == [
        f"x1: request longer than {LONGEST_REQUEST} characters"
    ]


def test_a_repeated_ident_is_reported_once():
    assert unique([rec("a"), rec("b"), rec("a"), rec("a")]) == ["a: repeated locator"]
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
    assert summarize(records) == f"2 bookings, guests 5, top {A}"
    assert records == before
    assert summarize([]) == "0 bookings, guests 0"


def test_the_report_is_summary_then_records_then_sorted_errors():
    text = "ec7,6,breakfast\nzz9,13,breakfast|bad\nec7,6,late"
    errors = sorted([
        f"ec7: repeated locator",
        f"zz9: unknown extras entry 'bad'",
        f"zz9: guests 13 is outside {LOWEST}-{HIGHEST}",
    ])
    expected = [
        f"3 bookings, guests {6 + 13 + 6}, top {A}",
        f"ec7 6 [{A}]",
        f"zz9 13 [{A}|bad]",
        f"ec7 6 [{B}]",
        *errors,
    ]
    assert run(text).splitlines() == expected
