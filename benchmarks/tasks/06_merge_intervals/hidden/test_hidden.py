import pytest
from intervals import merge_intervals


def test_unsorted_input():
    assert merge_intervals([(8, 10), (1, 3), (2, 6), (15, 18)]) == [(1, 6), (8, 10), (15, 18)]


def test_touching_intervals_merge():
    assert merge_intervals([(1, 3), (3, 5)]) == [(1, 5)]


def test_contained_interval():
    assert merge_intervals([(1, 10), (2, 3), (4, 5)]) == [(1, 10)]


def test_empty_and_single():
    assert merge_intervals([]) == []
    assert merge_intervals([(2, 2)]) == [(2, 2)]


def test_accepts_lists_and_floats():
    assert merge_intervals([[0.5, 1.5], [1.0, 2.0]]) == [(0.5, 2.0)]


def test_input_not_modified():
    data = [(5, 6), (1, 2)]
    merge_intervals(data)
    assert data == [(5, 6), (1, 2)]


def test_invalid_interval():
    with pytest.raises(ValueError):
        merge_intervals([(1, 2), (5, 3)])


def test_generator_input():
    assert merge_intervals((i, i + 1) for i in range(0, 6, 2)) == [(0, 1), (2, 3), (4, 5)]
