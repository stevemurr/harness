from fleet.alpha.format import render, summarize, top_tag
from fleet.alpha.models import Shipment
from fleet.alpha.parse import parse, parse_line
from fleet.alpha.pipeline import run
from fleet.alpha.rules import ALLOWED, HIGHEST, LONGEST_REMARK, LOWEST, check, unique

A, B = sorted(ALLOWED)[:2]
GOOD = "al7,250, Bulk | cold ,fine"


def rec(ident="x1", amount=250, tags=(), note=""):
    return Shipment(ident, amount, tuple(tags), note)


def test_parse_reads_a_line_and_normalises_tags():
    record = parse_line(GOOD, 1)
    assert record.code == "al7"
    assert record.weight == 250
    assert record.labels == (A, B)
    assert record.remark == "fine"


def test_a_note_is_optional_and_defaults_to_empty():
    assert parse_line("al7,250,bulk", 1).remark == ""
    assert Shipment("x1", 250, ()).remark == ""


def test_a_negative_amount_is_kept_as_negative_for_the_rules_to_see():
    assert parse_line("al7,-3,bulk", 1).weight == -3


def test_blank_lines_anywhere_are_skipped_and_parsing_continues():
    records, errors = parse("al7,250,bulk\n\n   \nal72,250,cold\n")
    assert [r.code for r in records] == ["al7", "al72"]
    assert errors == []


def test_a_bad_line_is_an_error_and_the_rest_is_still_read():
    records, errors = parse("al7,250,bulk\nnope\nal72,250,cold")
    assert len(records) == 2
    assert errors == ["line 2: expected code,weight,labels[,remark]"]


def test_the_range_is_inclusive_at_both_ends():
    assert check(rec(amount=LOWEST)) == []
    assert check(rec(amount=HIGHEST)) == []
    outside = f"is outside {LOWEST}-{HIGHEST}"
    assert check(rec(amount=HIGHEST + 1)) == [f"x1: weight {HIGHEST + 1} {outside}"]
    assert check(rec(amount=LOWEST - 1)) == [f"x1: weight {LOWEST - 1} {outside}"]


def test_only_unknown_tags_are_errors():
    assert check(rec(tags=(A, B))) == []
    assert check(rec(tags=(A, "zzz"))) == ["x1: unknown labels entry 'zzz'"]


def test_the_note_may_be_exactly_the_longest_allowed():
    assert check(rec(note="n" * LONGEST_REMARK)) == []
    assert check(rec(note="n" * (LONGEST_REMARK + 1))) == [
        f"x1: remark longer than {LONGEST_REMARK} characters"
    ]


def test_a_repeated_ident_is_reported_once():
    assert unique([rec("a"), rec("b"), rec("a"), rec("a")]) == ["a: repeated code"]
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
    assert summarize(records) == f"2 shipments, weight 5, top {A}"
    assert records == before
    assert summarize([]) == "0 shipments, weight 0"


def test_the_report_is_summary_then_records_then_sorted_errors():
    text = "al7,250,bulk\nzz9,501,bulk|bad\nal7,250,cold"
    errors = sorted([
        f"al7: repeated code",
        f"zz9: unknown labels entry 'bad'",
        f"zz9: weight 501 is outside {LOWEST}-{HIGHEST}",
    ])
    expected = [
        f"3 shipments, weight {250 + 501 + 250}, top {A}",
        f"al7 250 [{A}]",
        f"zz9 501 [{A}|bad]",
        f"al7 250 [{B}]",
        *errors,
    ]
    assert run(text).splitlines() == expected
