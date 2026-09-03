from fleet.kilo.format import render, summarize, top_tag
from fleet.kilo.models import Vehicle
from fleet.kilo.parse import parse, parse_line
from fleet.kilo.pipeline import run
from fleet.kilo.rules import ALLOWED, HIGHEST, LONGEST_DEFECT, LOWEST, check, unique

A, B = sorted(ALLOWED)[:2]
GOOD = "ki7,200000, Brakes | engine ,fine"


def rec(ident="x1", amount=200000, tags=(), note=""):
    return Vehicle(ident, amount, tuple(tags), note)


def test_parse_reads_a_line_and_normalises_tags():
    record = parse_line(GOOD, 1)
    assert record.plate == "ki7"
    assert record.mileage == 200000
    assert record.systems == (A, B)
    assert record.defect == "fine"


def test_a_note_is_optional_and_defaults_to_empty():
    assert parse_line("ki7,200000,brakes", 1).defect == ""
    assert Vehicle("x1", 200000, ()).defect == ""


def test_a_negative_amount_is_kept_as_negative_for_the_rules_to_see():
    assert parse_line("ki7,-3,brakes", 1).mileage == -3


def test_blank_lines_anywhere_are_skipped_and_parsing_continues():
    records, errors = parse("ki7,200000,brakes\n\n   \nki72,200000,engine\n")
    assert [r.plate for r in records] == ["ki7", "ki72"]
    assert errors == []


def test_a_bad_line_is_an_error_and_the_rest_is_still_read():
    records, errors = parse("ki7,200000,brakes\nnope\nki72,200000,engine")
    assert len(records) == 2
    assert errors == ["line 2: expected plate,mileage,systems[,defect]"]


def test_the_range_is_inclusive_at_both_ends():
    assert check(rec(amount=LOWEST)) == []
    assert check(rec(amount=HIGHEST)) == []
    outside = f"is outside {LOWEST}-{HIGHEST}"
    assert check(rec(amount=HIGHEST + 1)) == [f"x1: mileage {HIGHEST + 1} {outside}"]
    assert check(rec(amount=LOWEST - 1)) == [f"x1: mileage {LOWEST - 1} {outside}"]


def test_only_unknown_tags_are_errors():
    assert check(rec(tags=(A, B))) == []
    assert check(rec(tags=(A, "zzz"))) == ["x1: unknown systems entry 'zzz'"]


def test_the_note_may_be_exactly_the_longest_allowed():
    assert check(rec(note="n" * LONGEST_DEFECT)) == []
    assert check(rec(note="n" * (LONGEST_DEFECT + 1))) == [
        f"x1: defect longer than {LONGEST_DEFECT} characters"
    ]


def test_a_repeated_ident_is_reported_once():
    assert unique([rec("a"), rec("b"), rec("a"), rec("a")]) == ["a: repeated plate"]
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
    assert summarize(records) == f"2 vehicles, mileage 5, top {A}"
    assert records == before
    assert summarize([]) == "0 vehicles, mileage 0"


def test_the_report_is_summary_then_records_then_sorted_errors():
    text = "ki7,200000,brakes\nzz9,400001,brakes|bad\nki7,200000,engine"
    errors = sorted([
        f"ki7: repeated plate",
        f"zz9: unknown systems entry 'bad'",
        f"zz9: mileage 400001 is outside {LOWEST}-{HIGHEST}",
    ])
    expected = [
        f"3 vehicles, mileage {200000 + 400001 + 200000}, top {A}",
        f"ki7 200000 [{A}]",
        f"zz9 400001 [{A}|bad]",
        f"ki7 200000 [{B}]",
        *errors,
    ]
    assert run(text).splitlines() == expected
