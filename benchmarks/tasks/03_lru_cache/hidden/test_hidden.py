import pytest
from lru import LRUCache


def test_evicts_least_recently_used():
    cache = LRUCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.get("a")
    cache.put("c", 3)
    assert cache.get("b") is None
    assert cache.get("a") == 1
    assert cache.get("c") == 3


def test_update_refreshes_recency():
    cache = LRUCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("a", 10)
    cache.put("c", 3)
    assert "b" not in cache
    assert cache.get("a") == 10
    assert len(cache) == 2


def test_contains_does_not_touch_order():
    cache = LRUCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    assert "a" in cache
    cache.put("c", 3)
    assert "a" not in cache
    assert "b" in cache


def test_default_value():
    cache = LRUCache(1)
    assert cache.get("missing", "fallback") == "fallback"


def test_capacity_one():
    cache = LRUCache(1)
    cache.put(1, "x")
    cache.put(2, "y")
    assert len(cache) == 1
    assert cache.get(2) == "y"


@pytest.mark.parametrize("capacity", [0, -3])
def test_invalid_capacity(capacity):
    with pytest.raises(ValueError):
        LRUCache(capacity)


def test_large_workload_is_fast():
    cache = LRUCache(1000)
    for i in range(200_000):
        cache.put(i, i)
        cache.get(i - 500)
    assert len(cache) == 1000
