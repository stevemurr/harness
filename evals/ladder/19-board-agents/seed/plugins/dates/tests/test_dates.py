import pytest

from dates.dates import add_days, days_between, is_weekend, parse_iso


def test_iso_dates_parse():
    assert parse_iso("2026-02-28").month == 2
    with pytest.raises(ValueError):
        parse_iso("28/02/2026")


def test_the_same_day_is_zero_days_apart():
    assert days_between("2026-03-01", "2026-03-01") == 0


def test_the_next_day_is_one_day_apart():
    assert days_between("2026-03-01", "2026-03-02") == 1


def test_days_across_a_leap_day():
    assert days_between("2028-02-28", "2028-03-01") == 2


def test_adding_days_and_weekends():
    assert add_days("2026-12-30", 3) == "2027-01-02"
    assert is_weekend("2026-09-05") and not is_weekend("2026-09-03")
