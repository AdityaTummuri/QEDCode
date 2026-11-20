"""The MCP server (AGENTS.md section 11): `qed mcp` over stdio, for Claude Code, OpenCode, Cline,
Cursor and other agents.

Four tools: solve, check, verify, status. Thin: it only calls qedcode.engine.api. One job per
project at a time (a second call gets status "busy"); solves run in a background thread and the
call either returns the result or `status: "running"` with a job id that `status` can poll.
Non-interactive rules apply: questions get no answer and nothing risky is approved, and the helper
model is never used unless the config pre-approves it.

stdout belongs to the protocol: nothing in this process may print to it.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from mcp.server.mcpserver import Context, MCPServer

from qedcode.config import GlobalConfig, load_global
from qedcode.engine import api
from qedcode.engine.callbacks import NonInteractiveCallbacks
from qedcode.engine.events import event_line
from qedcode.engine.lock import lock_holder
from qedcode.models.schemas import Event, SolveResult, Speed
from qedcode.runtime.client import ModelClient
from qedcode.runtime.quiet import effective_keep_alive

log = logging.getLogger("qedcode.mcp")

DEFAULT_WAIT_SECONDS = 600
POLL_SECONDS = 0.25
PROGRESS_LINES_SHOWN = 12
KEEP_FINISHED_JOBS = 50  # finished jobs kept for `status`; older ones are forgotten
INSTRUCTIONS = (
    "QEDCode fixes bugs and builds small features in a Python or JavaScript/TypeScript project "
    "with several tries, tests on every try, and one proven result with a grade (A proven, "
    "B tested, C checked, D unchecked) and a proof receipt. Solves take minutes: if `solve` "
    "returns status 'running', poll `status` with its job_id. Use `check` to grade changes you "
    "wrote yourself (no model needed), and `verify` to re-run a receipt. Pass `project_dir` (the "
    "project's absolute path) and read `notes`: they say when the tests could not run."
)


class Busy(Exception):  # noqa: N818 - control flow
    """A job is already running in this project."""

    def __init__(self, message: str, job_id: str = "") -> None:
        super().__init__(message)
        self.job_id = job_id


@dataclass
class Job:
    """One background job (a solve or a check)."""

    id: str
    kind: str
    root: Path
    started: float = field(default_factory=time.monotonic)
    finished: float = 0.0
    events: list[str] = field(default_factory=list)
    result: dict | None = None
    done: threading.Event = field(default_factory=threading.Event)
    cancel: threading.Event = field(default_factory=threading.Event)

    def add_event(self, event: Event) -> None:
        line = event_line(event)
        if line:
            self.events.append(line)


class JobRunner:
    """Runs jobs in threads; refuses a second job in the same project."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._active: dict[str, str] = {}
        self._lock = threading.Lock()

    def start(self, kind: str, root: Path, target: Callable[[Job], dict]) -> Job:
        key = str(root.resolve())
        with self._lock:
            running = self._jobs.get(self._active.get(key, ""))
            if running is not None and not running.done.is_set():
                raise Busy(
                    f"A QEDCode {running.kind} is already running in this project "
                    f"(job {running.id}). Wait for it, or poll `status` with that job id.",
                    running.id,
                )
            holder = lock_holder(root)
            if holder is not None:
                who = f"process {holder['pid']}" if holder.get("pid") else "it is just starting"
                raise Busy(
                    f"Another QEDCode task is running in this project ({who}), for example in "
                    "the terminal. Wait for it to finish."
                )
            self._forget_old()
            job = Job(id=uuid.uuid4().hex[:10], kind=kind, root=root)
            self._jobs[job.id] = job
            self._active[key] = job.id

        def run() -> None:
            try:
                job.result = target(job)
            except Exception as exc:  # noqa: BLE001 - a job must always finish with a result
                log.exception("MCP %s job %s crashed", kind, job.id)
                job.result = {
                    "status": "error",
                    "message": f"Something went wrong inside QEDCode ({type(exc).__name__}). "
                    "The details are in ~/.qedcode/logs/qedcode.log; run `qed doctor`.",
                }
            finally:
                job.finished = time.monotonic()
                job.done.set()
                with self._lock:
                    if self._active.get(key) == job.id:
                        del self._active[key]

        threading.Thread(target=run, name=f"qed-job-{job.id}", daemon=True).start()
        return job

    def _forget_old(self) -> None:
        """Keep only the newest finished jobs, so a long-running server does not grow forever."""
        finished = sorted(
            (j for j in self._jobs.values() if j.done.is_set()), key=lambda j: j.finished
        )
        for job in finished[: max(len(finished) - KEEP_FINISHED_JOBS, 0)]:
            del self._jobs[job.id]

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def cancel_all(self) -> None:
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            job.cancel.set()


def solve_payload(result: SolveResult, job_id: str = "") -> dict:
    """The dict a `solve` call returns (status, grade, summary, explanation, diff, receipt...)."""
    status = result.status
    if status == "error" and "already running" in result.message:
        status = "busy"
    best = result.best
    return {
        "status": status,
        "job_id": job_id,
        "grade": best.grade.value if best and best.grade else None,
        "summary": result.message,
        "explanation": result.explanation,
        "diff": best.diff if best else "",
        "receipt_path": result.receipt_path,
        "question": result.question if status in ("needs_input", "no_fix") else None,
        "message": result.message,
    }


class QedMcpService:
    """The four tools as plain async methods (the MCP layer only forwards to them)."""

    def __init__(
        self,
        *,
        client_factory: Callable[[GlobalConfig], ModelClient] | None = None,
        config_loader: Callable[[], GlobalConfig] = load_global,
        runner: JobRunner | None = None,
    ) -> None:
        self.client_factory = client_factory
        self.config_loader = config_loader
        self.runner = runner or JobRunner()
        self._watcher_started = False

    # ------------------------------------------------------------------ helpers

    def _root(self, project_dir: str) -> Path:
        return api.resolve_root(project_dir or None)

    @staticmethod
    def _speed(value: str) -> Speed | None:
        try:
            return Speed(value.lower())
        except ValueError:
            return None

    def _ensure_idle_unload(self, cfg: GlobalConfig) -> None:
        """Let the shared server unload itself when idle (the model loads lazily on first use)."""
        if self._watcher_started or self.client_factory is not None:
            return
        if cfg.local.backend != "llamacpp" or cfg.engine.mode.value == "cloud":
            return
        from qedcode.runtime.server_manager import ServerManager

        ServerManager().start_idle_watcher(effective_keep_alive(cfg))
        self._watcher_started = True

    async def _wait(self, job: Job, seconds: float, ctx: Context | None) -> bool:
        """Wait up to `seconds` for the job, reporting progress when the client wants it."""
        deadline = time.monotonic() + max(seconds, 0)
        reported = 0
        while not job.done.is_set() and time.monotonic() < deadline:
            await asyncio.sleep(POLL_SECONDS)
            if ctx is not None and len(job.events) > reported:
                reported = len(job.events)
                try:
                    await ctx.report_progress(reported, None, job.events[-1])
                except Exception:  # noqa: BLE001 - the client may not support progress
                    ctx = None
        return job.done.is_set()

    @staticmethod
    def _busy(exc: Busy) -> dict:
        return {"status": "busy", "job_id": exc.job_id, "message": str(exc), "summary": str(exc)}

    # ------------------------------------------------------------------ the tools

    async def solve(
        self,
        task: str,
        speed: str = "normal",
        apply: bool = False,
        wait_seconds: int = DEFAULT_WAIT_SECONDS,
        project_dir: str = "",
        ctx: Context | None = None,
    ) -> dict:
        chosen = self._speed(speed)
        if chosen is None:
            return {"status": "error", "message": "speed must be fast, normal or sure."}
        if not task.strip():
            return {"status": "error", "message": "Say what to do in `task`."}
        root = self._root(project_dir)
        cfg = self.config_loader()
        self._ensure_idle_unload(cfg)

        def run(job: Job) -> dict:
            cb = NonInteractiveCallbacks(on_event=job.add_event, cancelled=job.cancel.is_set)
            client = self.client_factory(cfg) if self.client_factory else None
            result = api.solve(
                root, task, speed=chosen, cb=cb, config=cfg, client=client, apply=apply
            )
            return solve_payload(result, job.id)

        try:
            job = self.runner.start("solve", root, run)
        except Busy as exc:
            return self._busy(exc)
        if await self._wait(job, wait_seconds, ctx):
            return {**(job.result or {}), "job_id": job.id}
        return {
            "status": "running",
            "job_id": job.id,
            "message": "Still working. Call `status` with this job_id to follow it.",
            "progress": job.events[-PROGRESS_LINES_SHOWN:],
        }

    async def check(
        self,
        speed: str = "normal",
        base: str = "HEAD",
        project_dir: str = "",
        ctx: Context | None = None,
    ) -> dict:
        chosen = self._speed(speed)
        if chosen is None:
            return {"status": "error", "message": "speed must be fast, normal or sure."}
        root = self._root(project_dir)

        def run(job: Job) -> dict:
            cb = NonInteractiveCallbacks(on_event=job.add_event, cancelled=job.cancel.is_set)
            outcome = api.check(root, speed=chosen, base=base, cb=cb)
            return {
                "status": outcome.status,
                "job_id": job.id,
                "grade": outcome.grade.value if outcome.grade else None,
                "summary": outcome.summary or outcome.message,
                "failing_tests": outcome.failing_tests,
                "receipt_path": outcome.receipt_path,
                "message": outcome.message,
                "notes": outcome.notes,
            }

        try:
            job = self.runner.start("check", root, run)
        except Busy as exc:
            return self._busy(exc)
        await self._wait(job, DEFAULT_WAIT_SECONDS, ctx)
        if job.done.is_set():
            return {**(job.result or {}), "job_id": job.id}
        return {
            "status": "running",
            "job_id": job.id,
            "progress": job.events[-PROGRESS_LINES_SHOWN:],
        }

    async def verify(self, receipt_path: str) -> dict:
        path = Path(receipt_path).expanduser()
        root = api.resolve_root(path.parent if path.parent.exists() else None)
        result = await asyncio.to_thread(api.verify, path, root)
        return {"verified": result.verified, "mismatches": result.mismatches, "notes": result.notes}

    async def status(self, job_id: str) -> dict:
        job = self.runner.get(job_id)
        if job is None:
            return {"status": "error", "message": f"There is no job called '{job_id}'."}
        if job.done.is_set():
            return {**(job.result or {}), "job_id": job.id, "progress": job.events[-30:]}
        return {
            "status": "running",
            "job_id": job.id,
            "progress": job.events[-30:],
            "seconds": round(time.monotonic() - job.started, 1),
        }

    def shutdown(self) -> None:
        """Cancel running jobs and release the shared model server."""
        self.runner.cancel_all()
        if self._watcher_started:
            from qedcode.runtime.server_manager import ServerManager

            manager = ServerManager()
            manager.stop_idle_watcher()
            manager.release(unload_on_exit=self.config_loader().engine.unload_on_exit)


def build_server(service: QedMcpService | None = None) -> MCPServer:
    """The MCP server with the four tools wired to a service."""
    service = service or QedMcpService()
    server = MCPServer("qedcode", instructions=INSTRUCTIONS)

    @server.tool()
    async def solve(
        task: str,
        ctx: Context,
        speed: str = "normal",
        apply: bool = False,
        wait_seconds: int = DEFAULT_WAIT_SECONDS,
        project_dir: str = "",
    ) -> dict:
        """Fix a bug or build a small feature in a Python or JS/TS project, tested on every try.

        Returns the best proven change with a grade (A proven, B tested, C checked, D unchecked),
        a plain-English explanation, the diff and a proof receipt path. Nothing in the user's
        files changes unless `apply` is true and the grade is A or B. A solve takes minutes: if
        the status is 'running', poll `status` with the job_id. speed is fast, normal or sure.
        """
        return await service.solve(task, speed, apply, wait_seconds, project_dir, ctx)

    @server.tool()
    async def check(
        ctx: Context, speed: str = "normal", base: str = "HEAD", project_dir: str = ""
    ) -> dict:
        """Grade the uncommitted changes in the project (for example code you just wrote).

        Runs the project's tests and checks, with no model involved, and returns a grade, a
        one-line summary, any failing tests, notes (for example when the tests could not run)
        and a proof receipt path.
        """
        return await service.check(speed, base, project_dir, ctx)

    @server.tool()
    async def verify(receipt_path: str) -> dict:
        """Re-run a QEDCode proof receipt and report whether it still verifies.

        Returns verified (true or false) and the list of things that no longer match.
        """
        return await service.verify(receipt_path)

    @server.tool()
    async def status(job_id: str) -> dict:
        """Progress of a running solve or check (by job_id), or its final result when finished."""
        return await service.status(job_id)

    return server


def run_stdio() -> None:
    """Run the MCP server over stdio until the client disconnects (`qed mcp`)."""
    service = QedMcpService()
    server = build_server(service)
    try:
        server.run("stdio")
    finally:
        service.shutdown()
