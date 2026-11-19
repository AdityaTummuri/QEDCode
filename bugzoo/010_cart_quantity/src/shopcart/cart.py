"""A tiny shopping cart."""


class Cart:
    """Holds items with a price and a quantity."""

    def __init__(self):
        self.items = []

    def add(self, name, price, quantity=1):
        """Put `quantity` of an item in the cart."""
        self.items.append({"name": name, "price": price, "quantity": quantity})

    def count(self):
        """Return how many things are in the cart."""
        return sum(item["quantity"] for item in self.items)

    def total(self):
        """Return the price of everything in the cart."""
        return sum(item["price"] for item in self.items)
