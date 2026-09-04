from decimal import Decimal

import pytest

from ledger.records import parse_line, read_records


def test_a_line_parses_into_a_record():
    record = parse_line("2026-01-03 | groceries | -42.10 | market")
    assert record.date == "2026-01-03"
    assert record.account == "groceries"
    assert record.amount == Decimal("-42.10")
    assert record.memo == "market"


def test_a_short_line_is_refused():
    with pytest.raises(ValueError):
        parse_line("2026-01-03 | groceries")


def test_the_file_is_read_in_order_without_comments(tmp_path):
    path = tmp_path / "l.txt"
    path.write_text("# header\n2026-01-01 | a | 1.00 | x\n\n2026-01-02 | b | 2.00 | y\n")
    assert [r.account for r in read_records(path)] == ["a", "b"]
