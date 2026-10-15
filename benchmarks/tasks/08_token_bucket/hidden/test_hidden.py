import pytest
from ratelimit import TokenBucket


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_drains_then_refuses():
    clock = FakeClock()
    bucket = TokenBucket(rate=1, capacity=3, clock=clock)
    assert [bucket.allow() for _ in range(4)] == [True, True, True, False]


def test_refills_over_time():
    clock = FakeClock()
    bucket = TokenBucket(rate=2, capacity=4, clock=clock)
    assert bucket.allow(4)
    assert not bucket.allow()
    clock.now += 0.5
    assert bucket.allow()
    assert not bucket.allow()
    clock.now += 1.0
    assert bucket.available() == pytest.approx(2.0)


def test_refill_caps_at_capacity():
    clock = FakeClock()
    bucket = TokenBucket(rate=10, capacity=5, clock=clock)
    bucket.allow(5)
    clock.now += 100
    assert bucket.available() == pytest.approx(5.0)


def test_failed_request_takes_nothing():
    clock = FakeClock()
    bucket = TokenBucket(rate=1, capacity=3, clock=clock)
    bucket.allow(2)
    assert not bucket.allow(2)
    assert bucket.available() == pytest.approx(1.0)


def test_request_larger_than_capacity():
    bucket = TokenBucket(rate=1, capacity=3, clock=FakeClock())
    assert not bucket.allow(4)


def test_fractional_rates():
    clock = FakeClock()
    bucket = TokenBucket(rate=0.5, capacity=1, clock=clock)
    assert bucket.allow()
    clock.now += 1
    assert not bucket.allow()
    clock.now += 1
    assert bucket.allow()


@pytest.mark.parametrize(("rate", "capacity"), [(0, 1), (1, 0), (-1, 5)])
def test_invalid_configuration(rate, capacity):
    with pytest.raises(ValueError):
        TokenBucket(rate=rate, capacity=capacity, clock=FakeClock())


@pytest.mark.parametrize("tokens", [0, -1])
def test_invalid_token_count(tokens):
    bucket = TokenBucket(rate=1, capacity=3, clock=FakeClock())
    with pytest.raises(ValueError):
        bucket.allow(tokens)
