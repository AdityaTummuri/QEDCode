from tagger.tags import add_tag, has_tag


def test_adds_to_a_given_list():
    assert add_tag("b", ["a"]) == ["a", "b"]


def test_a_single_default_call_returns_just_the_tag():
    assert add_tag("solo") == ["solo"]


def test_has_tag():
    assert has_tag("a", ["a", "b"])
    assert not has_tag("z", ["a", "b"])
