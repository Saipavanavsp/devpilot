Implement `to_roman(number)` and `from_roman(numeral)` in `roman.py`.

- `to_roman` accepts integers from 1 to 3999 and returns the standard subtractive form ("IV", "XC", "MCMXCIV"). Raise `ValueError` for anything outside that range.
- `from_roman` converts a numeral back to an integer. It must reject invalid or non-canonical numerals such as "IIII", "IC", "VV", "" or lowercase input by raising `ValueError`. A numeral is valid only if `to_roman(from_roman(s)) == s`.

Add tests in `tests/test_roman.py`.
