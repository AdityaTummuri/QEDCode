from stats.core import average


def test_average_is_not_rounded_down():
    assert average([1, 2]) == 1.5


def test_average_of_three_numbers():
    assert abs(average([1, 1, 2]) - 4 / 3) < 1e-9
