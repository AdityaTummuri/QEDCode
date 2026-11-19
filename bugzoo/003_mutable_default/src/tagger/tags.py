"""Tagging helpers."""


def add_tag(tag, tags=[]):
    """Add `tag` to `tags` and return the list; with no list given, start a new one."""
    tags.append(tag)
    return tags


def has_tag(tag, tags):
    """Return True when `tag` is in `tags`."""
    return tag in tags
