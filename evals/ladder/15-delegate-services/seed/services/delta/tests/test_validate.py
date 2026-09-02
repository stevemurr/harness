from services.delta.validate import validate


def test_a_good_date_has_no_errors():
    assert validate("2024-03-15") == []


def test_the_last_day_of_a_long_month_is_fine():
    assert validate("2024-01-31") == []


def test_the_thirty_first_of_a_short_month_is_not():
    assert validate("2024-04-31") == ["day: must be 1-30"]


def test_a_bad_shape_is_one_error():
    assert validate("15/03/2024") == ["date: must be YYYY-MM-DD"]
