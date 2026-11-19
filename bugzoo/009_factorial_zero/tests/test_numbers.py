import pytest

from mathx.numbers import factorial


def test_small_factorials():
    assert factorial(1) == 1
    assert factorial(5) == 120


def test_negative_numbers_are_rejected():
    with pytest.raises(ValueError):
        factorial(-1)
