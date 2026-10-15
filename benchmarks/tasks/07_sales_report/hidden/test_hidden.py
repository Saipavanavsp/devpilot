import pytest
from sales import summarize


def test_extra_columns_and_order():
    text = "date,amount,region,rep\n2024-01-01,10,West,ann\n2024-01-02,5.556,East,bob\n"
    assert summarize(text) == {"East": 5.56, "West": 10.0}


def test_keys_sorted():
    text = "region,amount\nZeta,1\nAlpha,2\nMid,3\n"
    assert list(summarize(text)) == ["Alpha", "Mid", "Zeta"]


def test_skips_bad_rows():
    text = "region,amount\nNorth,10\nNorth,abc\n,5\nSouth,\nSouth\nSouth,4\n"
    assert summarize(text) == {"North": 10.0, "South": 4.0}


def test_whitespace_is_trimmed():
    text = " region , amount \n North , 1.5 \nNorth,2\n"
    assert summarize(text) == {"North": 3.5}


def test_quoted_values_with_commas():
    text = 'region,amount,note\n"North",7,"late, but paid"\n'
    assert summarize(text) == {"North": 7.0}


def test_rounding_of_totals():
    text = "region,amount\nA,0.1\nA,0.2\n"
    assert summarize(text) == {"A": 0.3}


def test_missing_columns_raise():
    with pytest.raises(ValueError):
        summarize("area,amount\nNorth,1\n")
    with pytest.raises(ValueError):
        summarize("region,total\nNorth,1\n")


def test_header_only():
    assert summarize("region,amount\n") == {}
