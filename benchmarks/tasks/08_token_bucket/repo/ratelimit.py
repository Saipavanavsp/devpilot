import time


class TokenBucket:
    def __init__(self, rate: float, capacity: float, clock=time.monotonic) -> None:
        raise NotImplementedError
