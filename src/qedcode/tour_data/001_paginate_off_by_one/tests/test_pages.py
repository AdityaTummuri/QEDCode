from shop.pages import page_count, paginate


def test_empty_items_give_an_empty_page():
    assert paginate([], 1, 10) == []


def test_page_past_the_end_is_empty():
    assert paginate([1, 2, 3], 5, 2) == []


def test_result_is_always_a_list():
    assert isinstance(paginate((1, 2, 3), 3, 2), list)


def test_page_count_rounds_up():
    assert page_count(5, 2) == 3
    assert page_count(4, 2) == 2


def test_page_count_of_nothing_is_zero():
    assert page_count(0, 10) == 0
