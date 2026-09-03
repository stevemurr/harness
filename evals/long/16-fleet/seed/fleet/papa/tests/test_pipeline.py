from fleet.papa.format import render, summarize, top_tag
from fleet.papa.models import Recipe
from fleet.papa.parse import parse, parse_line
from fleet.papa.pipeline import run
from fleet.papa.rules import ALLOWED, HIGHEST, LONGEST_TIP, LOWEST, check, unique

A, B = sorted(ALLOWED)[:2]
GOOD = "pa7,240, Dairy | gluten ,fine"


def rec(ident="x1", amount=240, tags=(), note=""):
    return Recipe(ident, amount, tuple(tags), note)


def test_parse_reads_a_line_and_normalises_tags():
    record = parse_line(GOOD, 1)
    assert record.dish == "pa7"
    assert record.minutes == 240
    assert record.diets == (A, B)
    assert record.tip == "fine"


def test_a_note_is_optional_and_defaults_to_empty():
    assert parse_line("pa7,240,dairy", 1).tip == ""
    assert Recipe("x1", 240, ()).tip == ""


def test_a_negative_amount_is_kept_as_negative_for_the_rules_to_see():
    assert parse_line("pa7,-3,dairy", 1).minutes == -3


def test_blank_lines_anywhere_are_skipped_and_parsing_continues():
    records, errors = parse("pa7,240,dairy\n\n   \npa72,240,gluten\n")
    assert [r.dish for r in records] == ["pa7", "pa72"]
    assert errors == []


def test_a_bad_line_is_an_error_and_the_rest_is_still_read():
    records, errors = parse("pa7,240,dairy\nnope\npa72,240,gluten")
    assert len(records) == 2
    assert errors == ["line 2: expected dish,minutes,diets[,tip]"]


def test_the_range_is_inclusive_at_both_ends():
    assert check(rec(amount=LOWEST)) == []
    assert check(rec(amount=HIGHEST)) == []
    outside = f"is outside {LOWEST}-{HIGHEST}"
    assert check(rec(amount=HIGHEST + 1)) == [f"x1: minutes {HIGHEST + 1} {outside}"]
    assert check(rec(amount=LOWEST - 1)) == [f"x1: minutes {LOWEST - 1} {outside}"]


def test_only_unknown_tags_are_errors():
    assert check(rec(tags=(A, B))) == []
    assert check(rec(tags=(A, "zzz"))) == ["x1: unknown diets entry 'zzz'"]


def test_the_note_may_be_exactly_the_longest_allowed():
    assert check(rec(note="n" * LONGEST_TIP)) == []
    assert check(rec(note="n" * (LONGEST_TIP + 1))) == [
        f"x1: tip longer than {LONGEST_TIP} characters"
    ]


def test_a_repeated_ident_is_reported_once():
    assert unique([rec("a"), rec("b"), rec("a"), rec("a")]) == ["a: repeated dish"]
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
    assert summarize(records) == f"2 recipes, minutes 5, top {A}"
    assert records == before
    assert summarize([]) == "0 recipes, minutes 0"


def test_the_report_is_summary_then_records_then_sorted_errors():
    text = "pa7,240,dairy\nzz9,481,dairy|bad\npa7,240,gluten"
    errors = sorted([
        f"pa7: repeated dish",
        f"zz9: unknown diets entry 'bad'",
        f"zz9: minutes 481 is outside {LOWEST}-{HIGHEST}",
    ])
    expected = [
        f"3 recipes, minutes {240 + 481 + 240}, top {A}",
        f"pa7 240 [{A}]",
        f"zz9 481 [{A}|bad]",
        f"pa7 240 [{B}]",
        *errors,
    ]
    assert run(text).splitlines() == expected
