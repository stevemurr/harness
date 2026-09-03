from fleet.bravo.format import render, summarize, top_tag
from fleet.bravo.models import Invoice
from fleet.bravo.parse import parse, parse_line
from fleet.bravo.pipeline import run
from fleet.bravo.rules import ALLOWED, HIGHEST, LONGEST_MEMO, LOWEST, check, unique

A, B = sorted(ALLOWED)[:2]
GOOD = "br7,4500, Credit | goods ,fine"


def rec(ident="x1", amount=4500, tags=(), note=""):
    return Invoice(ident, amount, tuple(tags), note)


def test_parse_reads_a_line_and_normalises_tags():
    record = parse_line(GOOD, 1)
    assert record.number == "br7"
    assert record.total == 4500
    assert record.categories == (A, B)
    assert record.memo == "fine"


def test_a_note_is_optional_and_defaults_to_empty():
    assert parse_line("br7,4500,credit", 1).memo == ""
    assert Invoice("x1", 4500, ()).memo == ""


def test_a_negative_amount_is_kept_as_negative_for_the_rules_to_see():
    assert parse_line("br7,-3,credit", 1).total == -3


def test_blank_lines_anywhere_are_skipped_and_parsing_continues():
    records, errors = parse("br7,4500,credit\n\n   \nbr72,4500,goods\n")
    assert [r.number for r in records] == ["br7", "br72"]
    assert errors == []


def test_a_bad_line_is_an_error_and_the_rest_is_still_read():
    records, errors = parse("br7,4500,credit\nnope\nbr72,4500,goods")
    assert len(records) == 2
    assert errors == ["line 2: expected number,total,categories[,memo]"]


def test_the_range_is_inclusive_at_both_ends():
    assert check(rec(amount=LOWEST)) == []
    assert check(rec(amount=HIGHEST)) == []
    outside = f"is outside {LOWEST}-{HIGHEST}"
    assert check(rec(amount=HIGHEST + 1)) == [f"x1: total {HIGHEST + 1} {outside}"]
    assert check(rec(amount=LOWEST - 1)) == [f"x1: total {LOWEST - 1} {outside}"]


def test_only_unknown_tags_are_errors():
    assert check(rec(tags=(A, B))) == []
    assert check(rec(tags=(A, "zzz"))) == ["x1: unknown categories entry 'zzz'"]


def test_the_note_may_be_exactly_the_longest_allowed():
    assert check(rec(note="n" * LONGEST_MEMO)) == []
    assert check(rec(note="n" * (LONGEST_MEMO + 1))) == [
        f"x1: memo longer than {LONGEST_MEMO} characters"
    ]


def test_a_repeated_ident_is_reported_once():
    assert unique([rec("a"), rec("b"), rec("a"), rec("a")]) == ["a: repeated number"]
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
    assert summarize(records) == f"2 invoices, total 5, top {A}"
    assert records == before
    assert summarize([]) == "0 invoices, total 0"


def test_the_report_is_summary_then_records_then_sorted_errors():
    text = "br7,4500,credit\nzz9,9001,credit|bad\nbr7,4500,goods"
    errors = sorted([
        f"br7: repeated number",
        f"zz9: unknown categories entry 'bad'",
        f"zz9: total 9001 is outside {LOWEST}-{HIGHEST}",
    ])
    expected = [
        f"3 invoices, total {4500 + 9001 + 4500}, top {A}",
        f"br7 4500 [{A}]",
        f"zz9 9001 [{A}|bad]",
        f"br7 4500 [{B}]",
        *errors,
    ]
    assert run(text).splitlines() == expected
