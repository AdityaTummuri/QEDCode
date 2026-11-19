"""Statistics helpers."""


def average(numbers):
    """Return the mean of a list of numbers; 0 for an empty list."""
    if not numbers:
        return 0
    return sum(numbers) // len(numbers)


def spread(numbers):
    """Return max minus min; 0 for an empty list."""
    if not numbers:
        return 0
    return max(numbers) - min(numbers)
