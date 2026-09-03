from fleet.hotel.format import render, summarize, top_tag
from fleet.hotel.models import Room
from fleet.hotel.parse import parse, parse_line
from fleet.hotel.pipeline import run
from fleet.hotel.rules import ALLOWED, HIGHEST, LONGEST_WISH, LOWEST, check, unique

A, B = sorted(ALLOWED)[:2]
GOOD = "ho7,15, Balcony | high ,fine"


def rec(ident="x1", amount=15, tags=(), note=""):
    return Room(ident, amount, tuple(tags), note)


def test_parse_reads_a_line_and_normalises_tags():
    record = parse_line(GOOD, 1)
    assert record.door == "ho7"
    assert record.nights == 15
    assert record.amenities == (A, B)
    assert record.wish == "fine"


def test_a_note_is_optional_and_defaults_to_empty():
    assert parse_line("ho7,15,balcony", 1).wish == ""
    assert Room("x1", 15, ()).wish == ""


def test_a_negative_amount_is_kept_as_negative_for_the_rules_to_see():
    assert parse_line("ho7,-3,balcony", 1).nights == -3


def test_blank_lines_anywhere_are_skipped_and_parsing_continues():
    records, errors = parse("ho7,15,balcony\n\n   \nho72,15,high\n")
    assert [r.door for r in records] == ["ho7", "ho72"]
    assert errors == []


def test_a_bad_line_is_an_error_and_the_rest_is_still_read():
    records, errors = parse("ho7,15,balcony\nnope\nho72,15,high")
    assert len(records) == 2
    assert errors == ["line 2: expected door,nights,amenities[,wish]"]


def test_the_range_is_inclusive_at_both_ends():
    assert check(rec(amount=LOWEST)) == []
    assert check(rec(amount=HIGHEST)) == []
    outside = f"is outside {LOWEST}-{HIGHEST}"
    assert check(rec(amount=HIGHEST + 1)) == [f"x1: nights {HIGHEST + 1} {outside}"]
    assert check(rec(amount=LOWEST - 1)) == [f"x1: nights {LOWEST - 1} {outside}"]


def test_only_unknown_tags_are_errors():
    assert check(rec(tags=(A, B))) == []
    assert check(rec(tags=(A, "zzz"))) == ["x1: unknown amenities entry 'zzz'"]


def test_the_note_may_be_exactly_the_longest_allowed():
    assert check(rec(note="n" * LONGEST_WISH)) == []
    assert check(rec(note="n" * (LONGEST_WISH + 1))) == [
        f"x1: wish longer than {LONGEST_WISH} characters"
    ]


def test_a_repeated_ident_is_reported_once():
    assert unique([rec("a"), rec("b"), rec("a"), rec("a")]) == ["a: repeated door"]
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
    assert summarize(records) == f"2 rooms, nights 5, top {A}"
    assert records == before
    assert summarize([]) == "0 rooms, nights 0"


def test_the_report_is_summary_then_records_then_sorted_errors():
    text = "ho7,15,balcony\nzz9,31,balcony|bad\nho7,15,high"
    errors = sorted([
        f"ho7: repeated door",
        f"zz9: unknown amenities entry 'bad'",
        f"zz9: nights 31 is outside {LOWEST}-{HIGHEST}",
    ])
    expected = [
        f"3 rooms, nights {15 + 31 + 15}, top {A}",
        f"ho7 15 [{A}]",
        f"zz9 31 [{A}|bad]",
        f"ho7 15 [{B}]",
        *errors,
    ]
    assert run(text).splitlines() == expected
