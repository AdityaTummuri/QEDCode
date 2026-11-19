from stats.core import average, spread


def test_average_of_nothing_is_zero():
    assert average([]) == 0


def test_average_of_evenly_divisible_numbers():
    assert average([2, 4]) == 3


def test_average_of_one_number():
    assert average([5]) == 5


def test_spread():
    assert spread([1, 5, 3]) == 4
    assert spread([]) == 0
