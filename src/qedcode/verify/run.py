"""The one subprocess helper (AGENTS.md sections 2 and 13).

List arguments only (never a shell), explicit cwd, timeout, scrubbed environment, captured output,
optional log file, cancel polling and a process-tree kill (psutil) so no child is left running.
Everything QEDCode runs on the host (git, pytest, ruff, llama-server, GPU probes) goes through here.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import psutil

SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", re.IGNORECASE)
OUTPUT_LIMIT = 1_000_000  # characters kept per stream in memory; the log file keeps all of it
_POLL_SECONDS = 0.1


@dataclass
class RunResult:
    """What happened when a command ran."""

    args: list[str]
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_s: float = 0.0
    timed_out: bool = False
    cancelled: bool = False
    log_path: str | None = None
    error: str = ""  # set when the command could not start (for example: not found)
    extra: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.cancelled and not self.error

    @property
    def output(self) -> str:
        """stdout and stderr together, for messages."""
        return (self.stdout + ("\n" if self.stdout and self.stderr else "") + self.stderr).strip()


def scrubbed_env(
    base: Mapping[str, str] | None = None, extra: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Copy the environment without secrets, then add `extra` (which is never scrubbed)."""
    source = os.environ if base is None else base
    env = {k: v for k, v in source.items() if not SECRET_NAME.search(k)}
    if extra:
        env.update(extra)
    return env


def kill_process_tree(pid: int, *, grace_s: float = 3.0) -> None:
    """Kill a process and all of its descendants. Safe when they are already gone."""
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    try:
        victims = parent.children(recursive=True)
    except psutil.Error:
        victims = []
    victims.append(parent)
    for proc in victims:
        try:
            proc.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(victims, timeout=grace_s)


def _popen_kwargs() -> dict:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _trim(text: str) -> str:
    if len(text) <= OUTPUT_LIMIT:
        return text
    half = OUTPUT_LIMIT // 2
    return text[:half] + "\n...[output trimmed]...\n" + text[-half:]


def _write_log(path: Path, result: RunResult, cwd: Path | None) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        status = (
            "timed out"
            if result.timed_out
            else "cancelled"
            if result.cancelled
            else f"exit {result.exit_code}"
        )
        path.write_text(
            f"$ {' '.join(result.args)}\ncwd: {cwd}\n{status} after {result.duration_s:.2f}s\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}\n"
            + (f"--- error ---\n{result.error}\n" if result.error else ""),
            encoding="utf-8",
        )
    except OSError:
        pass  # a log we cannot write must never fail the run


def run(
    args: Sequence[str | os.PathLike[str]],
    *,
    cwd: Path | str | None = None,
    timeout: float | None = 60.0,
    env: Mapping[str, str] | None = None,
    scrub: bool = True,
    log_path: Path | str | None = None,
    stdin_text: str | None = None,
    cancelled: Callable[[], bool] | None = None,
    exact: bool = False,
) -> RunResult:
    """Run one command (list args, no shell) and always return a RunResult; never raises.

    `env` entries are added after scrubbing. A timeout or a True from `cancelled()` kills the whole
    process tree. Output is read as bytes (line endings are never translated); `exact=True` keeps
    undecodable bytes so a diff can be fed back to `git apply` unchanged.
    """
    argv = [os.fspath(a) for a in args]
    cwd_path = Path(cwd) if cwd is not None else None
    run_env = scrubbed_env(extra=env) if scrub else {**os.environ, **(env or {})}
    result = RunResult(args=argv)
    started = time.monotonic()
    try:
        proc = subprocess.Popen(  # noqa: S603 - list args, no shell, by design
            argv,
            cwd=cwd_path,
            env=run_env,
            stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **_popen_kwargs(),
        )
    except (OSError, ValueError) as exc:
        result.error = f"Could not start '{argv[0] if argv else ''}': {exc}"
        result.duration_s = time.monotonic() - started
        if log_path:
            _write_log(Path(log_path), result, cwd_path)
        return result

    errors = "surrogateescape" if exact else "replace"
    deadline = None if timeout is None else started + timeout
    pending_input = None if stdin_text is None else stdin_text.encode("utf-8", "surrogateescape")
    out = err = b""
    while True:
        try:
            out, err = proc.communicate(input=pending_input, timeout=_POLL_SECONDS)
            break
        except subprocess.TimeoutExpired:
            pending_input = None  # input is handed over on the first call only
            if cancelled is not None and cancelled():
                result.cancelled = True
            elif deadline is not None and time.monotonic() >= deadline:
                result.timed_out = True
            else:
                continue
            kill_process_tree(proc.pid)
            try:
                out, err = proc.communicate(timeout=5)
            except (subprocess.TimeoutExpired, ValueError):
                out = err = b""
            break
    result.exit_code = proc.returncode
    result.stdout = _trim((out or b"").decode("utf-8", errors))
    result.stderr = _trim((err or b"").decode("utf-8", "replace"))
    result.duration_s = time.monotonic() - started
    if log_path:
        _write_log(Path(log_path), result, cwd_path)
        result.log_path = str(log_path)
    return result


def spawn_detached(
    args: Sequence[str | os.PathLike[str]],
    *,
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    log_path: Path | str | None = None,
    scrub: bool = True,
) -> subprocess.Popen:
    """Start a long-lived background process (the model server) and return its Popen.

    Output is appended to `log_path` (or discarded). The caller owns the process and must stop it
    with `kill_process_tree`. List args only; never a shell.
    """
    argv = [os.fspath(a) for a in args]
    run_env = scrubbed_env(extra=env) if scrub else {**os.environ, **(env or {})}
    sink: object = subprocess.DEVNULL
    handle = None
    if log_path is not None:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        handle = open(log_path, "ab")  # noqa: SIM115 - closed below, after the child has it
        sink = handle
    kwargs: dict = {"start_new_session": True}
    if sys.platform == "win32":
        kwargs = {
            "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        }
    try:
        return subprocess.Popen(  # noqa: S603 - list args, no shell, by design
            argv,
            cwd=Path(cwd) if cwd is not None else None,
            env=run_env,
            stdin=subprocess.DEVNULL,
            stdout=sink,  # type: ignore[arg-type]
            stderr=sink,  # type: ignore[arg-type]
            **kwargs,
        )
    finally:
        if handle is not None:
            handle.close()
