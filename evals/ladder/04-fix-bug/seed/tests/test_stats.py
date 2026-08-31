from stats import mean, median, spread


def test_mean():
    assert mean([1, 2, 3, 4]) == 2.5


def test_median_odd():
    assert median([3, 1, 2]) == 2


def test_median_even():
    assert median([1, 2, 3, 4]) == 2.5


def test_median_even_longer():
    assert median([1, 2, 3, 4, 5, 6]) == 3.5


def test_spread():
    assert spread([4, 1, 9]) == 8
