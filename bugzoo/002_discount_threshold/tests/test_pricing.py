from store.pricing import apply_discount, cart_total


def test_small_orders_pay_full_price():
    assert apply_discount(50) == 50


def test_big_orders_get_ten_percent_off():
    assert apply_discount(200) == 180.0


def test_empty_cart_costs_nothing():
    assert cart_total([]) == 0


def test_cart_total_adds_prices():
    assert cart_total([10, 20]) == 30
