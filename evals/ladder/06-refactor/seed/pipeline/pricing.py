"""Money, which has nothing to do with text steps."""


class Discount:
    """A percentage off a price."""

    def __init__(self, percent: float) -> None:
        self.percent = percent

    def apply(self, price: float) -> float:
        return price - price * self.percent / 100


def total(prices: list[float], discount: "Discount") -> float:
    return sum(discount.apply(price) for price in prices)
