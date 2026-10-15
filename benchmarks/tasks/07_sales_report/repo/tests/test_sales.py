from sales import summarize


def test_simple_report():
    assert summarize("region,amount\nNorth,10\nSouth,5\nNorth,2.5\n") == {
        "North": 12.5,
        "South": 5.0,
    }
