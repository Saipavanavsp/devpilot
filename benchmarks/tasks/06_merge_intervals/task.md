Implement `merge_intervals(intervals)` in `intervals.py`.

Input is an iterable of `(start, end)` pairs of numbers, in any order. Return a new list of merged, non-overlapping `(start, end)` tuples sorted by start.
- Intervals that overlap or touch (`(1, 3)` and `(3, 5)`) are merged.
- Raise `ValueError` if any interval has `start > end`.
- Do not modify the input.

Add tests in `tests/test_intervals.py`.
