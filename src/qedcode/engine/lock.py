"""One job per project at a time: a lock file at <repo>/.qed/lock (AGENTS.md sections 6 and 11)."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psutil

LOCK_NAME = "lock"


class ProjectBusy(RuntimeError):
    """Another QEDCode job is already running in this project."""


def lock_path(root: Path | str) -> Path:
    return Path(root) / ".qed" / LOCK_NAME


def _holder(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def lock_holder(root: Path | str) -> dict | None:
    """Who holds the project lock (pid, since, task), or None when it is free or stale."""
    info = _holder(lock_path(root))
    if info and psutil.pid_exists(int(info.get("pid", 0))):
        return info
    return None


@contextmanager
def project_lock(root: Path | str, task: str = "") -> Iterator[None]:
    """Hold the project lock while a job runs; raises ProjectBusy if a live job holds it."""
    path = lock_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            holder = lock_holder(root)
            if holder is None:
                path.unlink(missing_ok=True)  # the owner died: take the lock over
                continue
            who = f"process {holder.get('pid')}"
            if holder.get("task"):
                who += f", task {holder['task']}"
            raise ProjectBusy(
                f"Another QEDCode task is already running in this project ({who}). "
                "Wait for it to finish, or press Esc in its window to cancel it."
            ) from None
        with os.fdopen(fd, "w") as handle:
            json.dump({"pid": os.getpid(), "since": time.time(), "task": task}, handle)
        break
    else:  # pragma: no cover - lost the race twice
        raise ProjectBusy(
            "Another QEDCode task is starting in this project. Try again in a moment."
        )
    try:
        yield
    finally:
        path.unlink(missing_ok=True)
