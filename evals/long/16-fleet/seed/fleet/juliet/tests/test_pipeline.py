from fleet.juliet.format import render, summarize, top_tag
from fleet.juliet.models import Batch
from fleet.juliet.parse import parse, parse_line
from fleet.juliet.pipeline import run
from fleet.juliet.rules import ALLOWED, HIGHEST, LONGEST_LOG, LOWEST, check, unique

A, B = sorted(ALLOWED)[:2]
GOOD = "ju7,50, Cure | cut ,fine"


def rec(ident="x1", amount=50, tags=(), note=""):
    return Batch(ident, amount, tuple(tags), note)


def test_parse_reads_a_line_and_normalises_tags():
    record = parse_line(GOOD, 1)
    assert record.lot == "ju7"
    assert record.output == 50
    assert record.stages == (A, B)
    assert record.log == "fine"


def test_a_note_is_optional_and_defaults_to_empty():
    assert parse_line("ju7,50,cure", 1).log == ""
    assert Batch("x1", 50, ()).log == ""


def test_a_negative_amount_is_kept_as_negative_for_the_rules_to_see():
    assert parse_line("ju7,-3,cure", 1).output == -3


def test_blank_lines_anywhere_are_skipped_and_parsing_continues():
    records, errors = parse("ju7,50,cure\n\n   \nju72,50,cut\n")
    assert [r.lot for r in records] == ["ju7", "ju72"]
    assert errors == []


def test_a_bad_line_is_an_error_and_the_rest_is_still_read():
    records, errors = parse("ju7,50,cure\nnope\nju72,50,cut")
    assert len(records) == 2
    assert errors == ["line 2: expected lot,output,stages[,log]"]


def test_the_range_is_inclusive_at_both_ends():
    assert check(rec(amount=LOWEST)) == []
    assert check(rec(amount=HIGHEST)) == []
    outside = f"is outside {LOWEST}-{HIGHEST}"
    assert check(rec(amount=HIGHEST + 1)) == [f"x1: output {HIGHEST + 1} {outside}"]
    assert check(rec(amount=LOWEST - 1)) == [f"x1: output {LOWEST - 1} {outside}"]


def test_only_unknown_tags_are_errors():
    assert check(rec(tags=(A, B))) == []
    assert check(rec(tags=(A, "zzz"))) == ["x1: unknown stages entry 'zzz'"]


def test_the_note_may_be_exactly_the_longest_allowed():
    assert check(rec(note="n" * LONGEST_LOG)) == []
    assert check(rec(note="n" * (LONGEST_LOG + 1))) == [
        f"x1: log longer than {LONGEST_LOG} characters"
    ]


def test_a_repeated_ident_is_reported_once():
    assert unique([rec("a"), rec("b"), rec("a"), rec("a")]) == ["a: repeated lot"]
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
    assert summarize(records) == f"2 batchs, output 5, top {A}"
    assert records == before
    assert summarize([]) == "0 batchs, output 0"


def test_the_report_is_summary_then_records_then_sorted_errors():
    text = "ju7,50,cure\nzz9,101,cure|bad\nju7,50,cut"
    errors = sorted([
        f"ju7: repeated lot",
        f"zz9: unknown stages entry 'bad'",
        f"zz9: output 101 is outside {LOWEST}-{HIGHEST}",
    ])
    expected = [
        f"3 batchs, output {50 + 101 + 50}, top {A}",
        f"ju7 50 [{A}]",
        f"zz9 101 [{A}|bad]",
        f"ju7 50 [{B}]",
        *errors,
    ]
    assert run(text).splitlines() == expected
