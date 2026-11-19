from shopcart.cart import Cart


def test_total_multiplies_by_quantity():
    cart = Cart()
    cart.add("pen", 2.5, 4)
    assert cart.total() == 10.0


def test_total_adds_several_lines():
    cart = Cart()
    cart.add("pen", 2.5, 2)
    cart.add("pad", 3, 1)
    assert cart.total() == 8.0
