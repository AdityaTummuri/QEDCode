from textutil.text import slugify


def test_lowercases_and_joins_with_dashes():
    assert slugify("Hello World") == "hello-world"


def test_punctuation_is_dropped():
    assert slugify("Hello, World!") == "hello-world"


def test_existing_dashes_are_kept():
    assert slugify("Already-slugged") == "already-slugged"
