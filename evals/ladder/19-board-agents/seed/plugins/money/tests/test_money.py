from money.money import format_cents, parse_dollars


def test_dollars_and_cents():
    assert format_cents(123456) == "$1,234.56"


def test_small_amounts_keep_two_places():
    assert format_cents(5) == "$0.05"


def test_negative_amounts_keep_their_cents():
    assert format_cents(-1250) == "-$12.50"
    assert format_cents(-5) == "-$0.05"


def test_parsing_reverses_formatting():
    for cents in (0, 5, 99, 100, 123456, -1250, -5):
        assert parse_dollars(format_cents(cents)) == cents


def test_parsing_is_forgiving():
    assert parse_dollars("1234.5") == 123450
    assert parse_dollars("-$3") == -300
