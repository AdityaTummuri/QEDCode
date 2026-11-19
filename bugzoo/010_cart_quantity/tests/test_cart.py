from shopcart.cart import Cart


def test_empty_cart_is_free():
    assert Cart().total() == 0


def test_one_item_costs_its_price():
    cart = Cart()
    cart.add("pen", 2.5)
    assert cart.total() == 2.5


def test_count_uses_quantities():
    cart = Cart()
    cart.add("pen", 2.5, 4)
    cart.add("pad", 3, 1)
    assert cart.count() == 5
