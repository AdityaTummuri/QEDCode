from mathx.numbers import choose, factorial


def test_factorial_of_zero_is_one():
    assert factorial(0) == 1


def test_choose_all_or_none():
    assert choose(5, 0) == 1
    assert choose(5, 5) == 1
