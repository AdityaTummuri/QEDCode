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


FRESH_SECONDS = 5.0  # a lock file this young may still be being written by its owner


def _holder(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _alive(info: dict) -> bool:
    """Is the process that wrote the lock still running (and not a new one with a reused pid)?"""
    try:
        pid = int(info.get("pid", 0))
        since = float(info.get("since", 0.0))
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        started = psutil.Process(pid).create_time()
    except psutil.Error:
        return False
    return not since or started <= since + 1.0


def _just_created(path: Path) -> bool:
    try:
        return time.time() - path.stat().st_mtime < FRESH_SECONDS
    except OSError:
        return False


def lock_holder(root: Path | str) -> dict | None:
    """Who holds the project lock (pid, since, task), or None when it is free or stale."""
    path = lock_path(root)
    info = _holder(path)
    if info is None:
        # Created but not written yet: its owner is between open() and write(). Busy, not stale.
        return {"pid": 0, "since": 0.0, "task": ""} if _just_created(path) else None
    return info if _alive(info) else None


def _release(path: Path, attempts: int = 40) -> None:
    """Delete the lock file, retrying briefly: on Windows a file another process is reading at
    that moment (`lock_holder` from a second job) cannot be deleted."""
    for attempt in range(attempts):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.025)


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
                try:
                    path.unlink(missing_ok=True)  # the owner died: take the lock over
                except OSError:
                    pass  # another job is taking it over right now; the next round decides
                continue
            who = f"process {holder['pid']}" if holder.get("pid") else "a task that is starting"
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
        _release(path)
