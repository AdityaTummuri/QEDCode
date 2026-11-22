"""Leaderboard helpers."""


def top_scores(scores, n=3):
    """Return the n highest scores, best first."""
    return sorted(scores)[:n]


def best_score(scores):
    """Return the single highest score, or None for no scores."""
    return max(scores) if scores else None
