Users report that `median()` in `stats.py` returns wrong answers, and that calling it changes the list they passed in. Find and fix the bugs.

Expected behaviour:
- Works for unsorted input of odd or even length; for even length it returns the mean of the two middle values.
- Does not modify the caller's list.
- Raises `ValueError` for an empty sequence.

Add regression tests to `tests/test_stats.py`.
