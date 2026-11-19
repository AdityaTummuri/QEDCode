"""Token estimates for prompt budgets: characters divided by 3.5 (AGENTS.md section 9)."""

from __future__ import annotations

import math

CHARS_PER_TOKEN = 3.5
PROMPT_MARGIN_TOKENS = 512


def estimate_tokens(text: str) -> int:
    """Rough token count of `text` (characters divided by 3.5, rounded up)."""
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def prompt_budget(context_tokens: int, max_output_tokens: int) -> int:
    """Tokens available for the prompt: context - max output - 512 (never below 256)."""
    return max(256, context_tokens - max_output_tokens - PROMPT_MARGIN_TOKENS)
