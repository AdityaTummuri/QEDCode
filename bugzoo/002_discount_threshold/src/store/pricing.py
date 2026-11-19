"""Order pricing."""

DISCOUNT_RATE = 0.10
DISCOUNT_THRESHOLD = 100


def apply_discount(total):
    """Orders of 100 or more get 10% off."""
    if total > DISCOUNT_THRESHOLD:
        return round(total * (1 - DISCOUNT_RATE), 2)
    return total


def cart_total(prices):
    """Sum the item prices, then apply the order discount."""
    return apply_discount(sum(prices))
