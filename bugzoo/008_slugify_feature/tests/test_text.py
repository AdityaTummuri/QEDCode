from textutil.text import truncate, word_count


def test_short_text_is_unchanged():
    assert truncate("hello", 10) == "hello"


def test_long_text_gets_an_ellipsis():
    assert truncate("hello world", 8) == "hello..."


def test_word_count():
    assert word_count("one two  three") == 3
    assert word_count("") == 0
