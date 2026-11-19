"""Task ids like t-20261120-153201-ab12."""

from __future__ import annotations

import secrets
import time


def new_task_id(now: float | None = None) -> str:
    """A sortable, unique-enough task id: t-<date>-<time>-<4 hex>."""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    return f"t-{stamp}-{secrets.token_hex(2)}"
