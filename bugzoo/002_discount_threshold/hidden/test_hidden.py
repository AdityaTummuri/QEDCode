from store.pricing import apply_discount, cart_total


def test_exactly_one_hundred_is_discounted():
    assert apply_discount(100) == 90.0


def test_cart_that_adds_up_to_one_hundred_is_discounted():
    assert cart_total([60, 40]) == 90.0


def test_just_below_the_threshold_is_not_discounted():
    assert apply_discount(99.99) == 99.99
