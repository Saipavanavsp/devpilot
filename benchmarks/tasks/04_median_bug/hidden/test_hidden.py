import pytest
from stats import mean, median


def test_unsorted_odd():
    assert median([9, 1, 5]) == 5


def test_even_length_averages_middle_values():
    assert median([4, 1, 3, 2]) == 2.5


def test_does_not_mutate_input():
    data = [3, 1, 2]
    median(data)
    assert data == [3, 1, 2]


def test_accepts_tuples():
    assert median((5, 3, 1)) == 3


def test_empty_raises():
    with pytest.raises(ValueError):
        median([])


def test_mean_still_works():
    assert mean([2, 4]) == 3
