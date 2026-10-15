from intervals import merge_intervals


def test_overlapping_pair():
    assert merge_intervals([(1, 3), (2, 4)]) == [(1, 4)]
