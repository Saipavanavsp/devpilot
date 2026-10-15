Implement a token bucket rate limiter in `ratelimit.py`.

`TokenBucket(rate, capacity, clock=time.monotonic)`:
- `rate` is tokens added per second and `capacity` is the maximum number of tokens. Both must be positive, otherwise raise `ValueError`.
- The bucket starts full.
- `allow(tokens=1)` refills the bucket based on time elapsed according to `clock()`, then, if enough tokens are available, removes them and returns `True`. Otherwise it returns `False` and removes nothing. Requests for more tokens than `capacity` always return `False`; `tokens` must be positive, otherwise raise `ValueError`.
- `available()` returns the current (refilled) number of tokens as a float.

The clock is injectable so tests can control time. Add tests in `tests/test_ratelimit.py`.
