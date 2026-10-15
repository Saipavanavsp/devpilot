`summarize(csv_text)` in `sales.py` crashes on real-world exports. Make it robust.

The CSV has a header row with at least the columns `region` and `amount` (in any order, possibly with other columns and surrounding whitespace in names or values).

Required behaviour:
- Return a dict mapping region to total amount, rounded to 2 decimal places, with keys in alphabetical order.
- Skip rows where the region is blank or the amount is missing or not a number, instead of crashing.
- Region names are trimmed and compared case-sensitively.
- Raise `ValueError` if the header is missing the `region` or `amount` column.

Add tests in `tests/test_sales.py`.
