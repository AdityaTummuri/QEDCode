from tagger.tags import add_tag


def test_calls_without_a_list_do_not_share_state():
    assert add_tag("a") == ["a"]
    assert add_tag("b") == ["b"]


def test_a_given_list_is_still_extended_in_place():
    mine = ["x"]
    assert add_tag("y", mine) is mine
    assert mine == ["x", "y"]
