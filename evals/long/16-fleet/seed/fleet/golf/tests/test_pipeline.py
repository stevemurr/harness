from fleet.golf.format import render, summarize, top_tag
from fleet.golf.models import Sample
from fleet.golf.parse import parse, parse_line
from fleet.golf.pipeline import run
from fleet.golf.rules import ALLOWED, HIGHEST, LONGEST_OBSERVATION, LOWEST, check, unique

A, B = sorted(ALLOWED)[:2]
GOOD = "go7,125, Frozen | plasma ,fine"


def rec(ident="x1", amount=125, tags=(), note=""):
    return Sample(ident, amount, tuple(tags), note)


def test_parse_reads_a_line_and_normalises_tags():
    record = parse_line(GOOD, 1)
    assert record.vial == "go7"
    assert record.volume == 125
    assert record.markers == (A, B)
    assert record.observation == "fine"


def test_a_note_is_optional_and_defaults_to_empty():
    assert parse_line("go7,125,frozen", 1).observation == ""
    assert Sample("x1", 125, ()).observation == ""


def test_a_negative_amount_is_kept_as_negative_for_the_rules_to_see():
    assert parse_line("go7,-3,frozen", 1).volume == -3


def test_blank_lines_anywhere_are_skipped_and_parsing_continues():
    records, errors = parse("go7,125,frozen\n\n   \ngo72,125,plasma\n")
    assert [r.vial for r in records] == ["go7", "go72"]
    assert errors == []


def test_a_bad_line_is_an_error_and_the_rest_is_still_read():
    records, errors = parse("go7,125,frozen\nnope\ngo72,125,plasma")
    assert len(records) == 2
    assert errors == ["line 2: expected vial,volume,markers[,observation]"]


def test_the_range_is_inclusive_at_both_ends():
    assert check(rec(amount=LOWEST)) == []
    assert check(rec(amount=HIGHEST)) == []
    outside = f"is outside {LOWEST}-{HIGHEST}"
    assert check(rec(amount=HIGHEST + 1)) == [f"x1: volume {HIGHEST + 1} {outside}"]
    assert check(rec(amount=LOWEST - 1)) == [f"x1: volume {LOWEST - 1} {outside}"]


def test_only_unknown_tags_are_errors():
    assert check(rec(tags=(A, B))) == []
    assert check(rec(tags=(A, "zzz"))) == ["x1: unknown markers entry 'zzz'"]


def test_the_note_may_be_exactly_the_longest_allowed():
    assert check(rec(note="n" * LONGEST_OBSERVATION)) == []
    assert check(rec(note="n" * (LONGEST_OBSERVATION + 1))) == [
        f"x1: observation longer than {LONGEST_OBSERVATION} characters"
    ]


def test_a_repeated_ident_is_reported_once():
    assert unique([rec("a"), rec("b"), rec("a"), rec("a")]) == ["a: repeated vial"]
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
    assert summarize(records) == f"2 samples, volume 5, top {A}"
    assert records == before
    assert summarize([]) == "0 samples, volume 0"


def test_the_report_is_summary_then_records_then_sorted_errors():
    text = "go7,125,frozen\nzz9,251,frozen|bad\ngo7,125,plasma"
    errors = sorted([
        f"go7: repeated vial",
        f"zz9: unknown markers entry 'bad'",
        f"zz9: volume 251 is outside {LOWEST}-{HIGHEST}",
    ])
    expected = [
        f"3 samples, volume {125 + 251 + 125}, top {A}",
        f"go7 125 [{A}]",
        f"zz9 251 [{A}|bad]",
        f"go7 125 [{B}]",
        *errors,
    ]
    assert run(text).splitlines() == expected
