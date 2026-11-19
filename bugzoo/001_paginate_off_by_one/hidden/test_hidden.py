from shop.pages import paginate


def test_full_pages_have_the_page_size():
    items = [1, 2, 3, 4, 5]
    assert paginate(items, 1, 2) == [1, 2]
    assert paginate(items, 2, 2) == [3, 4]


def test_last_partial_page_keeps_its_last_item():
    assert paginate([1, 2, 3, 4, 5], 3, 2) == [5]


def test_page_size_of_one():
    assert paginate(["a", "b"], 2, 1) == ["b"]
