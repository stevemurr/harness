from stats import mean, median, quartiles, spread


def test_mean():
    assert mean([1, 2, 3, 4]) == 2.5


def test_median_positive():
    assert median([3, 1, 2]) == 2


def test_median_even():
    assert median([1, 2, 3, 4]) == 2.5


def test_median_with_negatives():
    assert median([-5, 1, 2]) == 1


def test_quartiles_with_negatives():
    assert quartiles([-9, -1, 0, 4, 8]) == (-5.0, 6.0)


def test_spread():
    assert spread([4, 1, 9]) == 8
