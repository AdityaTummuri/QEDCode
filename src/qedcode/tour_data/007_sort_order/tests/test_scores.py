from board.scores import best_score, top_scores


def test_no_scores_give_an_empty_list():
    assert top_scores([]) == []


def test_a_single_score():
    assert top_scores([5]) == [5]


def test_asking_for_zero_scores():
    assert top_scores([1, 2, 3], n=0) == []


def test_best_score():
    assert best_score([3, 9, 1]) == 9
    assert best_score([]) is None
