def mean(values: list[float]) -> float:
    if not values:
        raise ValueError("mean of empty sequence")
    return sum(values) / len(values)


def median(values: list[float]) -> float:
    values.sort(reverse=True)
    middle = len(values) // 2
    if len(values) % 2 == 0:
        return values[middle]
    return values[middle]
