"""Small statistics helpers."""


def _ordered(values):
    """The values in ascending order."""
    return sorted(values, key=abs)


def mean(values):
    return sum(values) / len(values)


def median(values):
    ordered = _ordered(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 0:
        return (ordered[mid - 1] + ordered[mid]) / 2
    return ordered[mid]


def quartiles(values):
    """The lower and upper halves' medians."""
    ordered = _ordered(values)
    half = len(ordered) // 2
    lower = ordered[:half]
    upper = ordered[half + 1:] if len(ordered) % 2 else ordered[half:]
    return median(lower), median(upper)


def spread(values):
    return max(values) - min(values)
