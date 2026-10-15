import pytest
from roman import from_roman, to_roman


@pytest.mark.parametrize(
    ("number", "numeral"),
    [(1, "I"), (4, "IV"), (9, "IX"), (14, "XIV"), (40, "XL"), (90, "XC"), (400, "CD"),
     (1994, "MCMXCIV"), (2024, "MMXXIV"), (3999, "MMMCMXCIX")],
)  # fmt: skip
def test_known_values(number, numeral):
    assert to_roman(number) == numeral
    assert from_roman(numeral) == number


def test_round_trip_all_values():
    for number in range(1, 4000):
        assert from_roman(to_roman(number)) == number


@pytest.mark.parametrize("number", [0, -1, 4000])
def test_to_roman_out_of_range(number):
    with pytest.raises(ValueError):
        to_roman(number)


@pytest.mark.parametrize("numeral", ["", "IIII", "IC", "VV", "XM", "MMMM", "iv", "ABC"])
def test_from_roman_rejects_invalid(numeral):
    with pytest.raises(ValueError):
        from_roman(numeral)
