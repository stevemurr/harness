from services.echo.validate import validate


def test_a_good_name_has_no_errors():
    assert validate("  Ada  ") == []


def test_padding_does_not_make_a_short_name_long_enough():
    assert validate("   a   ") == ["name: at least three characters"]


def test_a_long_name_is_an_error():
    assert validate("x" * 21) == ["name: at most twenty characters"]


def test_not_text_is_one_error():
    assert validate(42) == ["name: must be text"]
