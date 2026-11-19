"""Paging helpers for product lists."""


def paginate(items, page, size):
    """Return the items on a 1-based page of the given size."""
    start = (page - 1) * size
    end = start + size - 1
    return list(items[start:end])


def page_count(total, size):
    """Return how many pages are needed for `total` items."""
    if total <= 0:
        return 0
    return (total + size - 1) // size
