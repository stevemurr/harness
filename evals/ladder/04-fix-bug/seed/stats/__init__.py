"""Small statistics helpers."""


def mean(values):
    return sum(values) / len(values)


def median(values):
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 0:
        return (ordered[mid] + ordered[mid + 1]) / 2
    return ordered[mid]


def spread(values):
    return max(values) - min(values)
