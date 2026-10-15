Implement the `LRUCache` class in `lru.py`, a fixed-size least-recently-used cache.

- `LRUCache(capacity)` raises `ValueError` if capacity is less than 1.
- `get(key, default=None)` returns the stored value (or `default`) and marks the key as most recently used.
- `put(key, value)` inserts or updates a key and marks it as most recently used. When the cache is over capacity it evicts the least recently used key.
- `len(cache)` returns the number of stored keys and `key in cache` works without changing the usage order.
- `get` and `put` must be O(1).

Add tests in `tests/test_lru.py`.
