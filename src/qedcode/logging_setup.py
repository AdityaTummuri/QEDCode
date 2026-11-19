"""Logging to ~/.qedcode/logs (AGENTS.md section 13). Rotate at 5 MB, keep 3 files."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from qedcode.config import logs_dir
from qedcode.runtime.secrets import MaskingFilter

MAX_LOG_BYTES = 5 * 1024 * 1024
KEEP_LOGS = 3
APP_LOG_NAME = "qedcode.log"
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def rotate_file(path: Path, max_bytes: int = MAX_LOG_BYTES, keep: int = KEEP_LOGS) -> bool:
    """If `path` is over `max_bytes`, shift path -> path.1 -> path.2 ... (keeping `keep` files)."""
    try:
        if not path.exists() or path.stat().st_size < max_bytes:
            return False
        oldest = path.with_name(f"{path.name}.{keep}")
        oldest.unlink(missing_ok=True)
        for index in range(keep - 1, 0, -1):
            older = path.with_name(f"{path.name}.{index}")
            if older.exists():
                older.replace(path.with_name(f"{path.name}.{index + 1}"))
        path.replace(path.with_name(f"{path.name}.1"))
        return True
    except OSError:
        return False


def setup_logging(level: int = logging.INFO) -> Path | None:
    """Send the `qedcode` loggers to a rotating file. Never prints; returns the log path."""
    logger = logging.getLogger("qedcode")
    logger.setLevel(level)
    try:
        path = logs_dir() / APP_LOG_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    for handler in logger.handlers:
        if getattr(handler, "_qed_file", False):
            return path
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=MAX_LOG_BYTES, backupCount=KEEP_LOGS, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(_FORMAT))
    handler.addFilter(MaskingFilter())
    handler._qed_file = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.propagate = False
    return path
