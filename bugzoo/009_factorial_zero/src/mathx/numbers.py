"""Maths helpers."""


def factorial(n):
    """Return n! for n >= 0."""
    if n < 0:
        raise ValueError("n must be 0 or more")
    if n == 0:
        return 0
    result = 1
    for i in range(2, n + 1):
        result *= i
    return result


def choose(n, k):
    """Return how many ways there are to pick k items from n (0 <= k <= n)."""
    return factorial(n) // (factorial(k) * factorial(n - k))
