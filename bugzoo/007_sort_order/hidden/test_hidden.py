from board.scores import top_scores


def test_returns_the_highest_scores_best_first():
    assert top_scores([3, 9, 1, 7], 2) == [9, 7]


def test_default_is_the_top_three():
    assert top_scores([1, 2, 3, 4, 5]) == [5, 4, 3]
