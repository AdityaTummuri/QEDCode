"""Small text helpers."""


def truncate(text, width):
    """Cut text to `width` characters, ending with '...' when it was shortened."""
    if len(text) <= width:
        return text
    return text[: max(width - 3, 0)] + "..."


def word_count(text):
    """Count the words in a piece of text."""
    return len(text.split())
