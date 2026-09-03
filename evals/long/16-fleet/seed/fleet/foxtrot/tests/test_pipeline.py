from fleet.foxtrot.format import render, summarize, top_tag
from fleet.foxtrot.models import Parcel
from fleet.foxtrot.parse import parse, parse_line
from fleet.foxtrot.pipeline import run
from fleet.foxtrot.rules import ALLOWED, HIGHEST, LONGEST_NOTES, LOWEST, check, unique

A, B = sorted(ALLOWED)[:2]
GOOD = "fo7,15005, Flat | liquid ,fine"


def rec(ident="x1", amount=15005, tags=(), note=""):
    return Parcel(ident, amount, tuple(tags), note)


def test_parse_reads_a_line_and_normalises_tags():
    record = parse_line(GOOD, 1)
    assert record.barcode == "fo7"
    assert record.grams == 15005
    assert record.handling == (A, B)
    assert record.notes == "fine"


def test_a_note_is_optional_and_defaults_to_empty():
    assert parse_line("fo7,15005,flat", 1).notes == ""
    assert Parcel("x1", 15005, ()).notes == ""


def test_a_negative_amount_is_kept_as_negative_for_the_rules_to_see():
    assert parse_line("fo7,-3,flat", 1).grams == -3


def test_blank_lines_anywhere_are_skipped_and_parsing_continues():
    records, errors = parse("fo7,15005,flat\n\n   \nfo72,15005,liquid\n")
    assert [r.barcode for r in records] == ["fo7", "fo72"]
    assert errors == []


def test_a_bad_line_is_an_error_and_the_rest_is_still_read():
    records, errors = parse("fo7,15005,flat\nnope\nfo72,15005,liquid")
    assert len(records) == 2
    assert errors == ["line 2: expected barcode,grams,handling[,notes]"]


def test_the_range_is_inclusive_at_both_ends():
    assert check(rec(amount=LOWEST)) == []
    assert check(rec(amount=HIGHEST)) == []
    outside = f"is outside {LOWEST}-{HIGHEST}"
    assert check(rec(amount=HIGHEST + 1)) == [f"x1: grams {HIGHEST + 1} {outside}"]
    assert check(rec(amount=LOWEST - 1)) == [f"x1: grams {LOWEST - 1} {outside}"]


def test_only_unknown_tags_are_errors():
    assert check(rec(tags=(A, B))) == []
    assert check(rec(tags=(A, "zzz"))) == ["x1: unknown handling entry 'zzz'"]


def test_the_note_may_be_exactly_the_longest_allowed():
    assert check(rec(note="n" * LONGEST_NOTES)) == []
    assert check(rec(note="n" * (LONGEST_NOTES + 1))) == [
        f"x1: notes longer than {LONGEST_NOTES} characters"
    ]


def test_a_repeated_ident_is_reported_once():
    assert unique([rec("a"), rec("b"), rec("a"), rec("a")]) == ["a: repeated barcode"]
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
    assert summarize(records) == f"2 parcels, grams 5, top {A}"
    assert records == before
    assert summarize([]) == "0 parcels, grams 0"


def test_the_report_is_summary_then_records_then_sorted_errors():
    text = "fo7,15005,flat\nzz9,30001,flat|bad\nfo7,15005,liquid"
    errors = sorted([
        f"fo7: repeated barcode",
        f"zz9: unknown handling entry 'bad'",
        f"zz9: grams 30001 is outside {LOWEST}-{HIGHEST}",
    ])
    expected = [
        f"3 parcels, grams {15005 + 30001 + 15005}, top {A}",
        f"fo7 15005 [{A}]",
        f"zz9 30001 [{A}|bad]",
        f"fo7 15005 [{B}]",
        *errors,
    ]
    assert run(text).splitlines() == expected
