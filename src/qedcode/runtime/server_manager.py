"""One shared llama-server per machine (AGENTS.md section 10, "Server manager").

The TUI, the CLI and the MCP server must not start three copies of a model on a 4-6 GB card, so
they all go through this manager:

- state file  ~/.qedcode/run/server.json   (pid, port, model, ctx, started_at, last_used)
- lock file   ~/.qedcode/run/server.lock   (guards starts, so two clients never start two servers)
- clients     ~/.qedcode/run/clients/<pid>.json
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import psutil
from pydantic import BaseModel

from qedcode.config import logs_dir, run_dir
from qedcode.logging_setup import rotate_file
from qedcode.runtime.client import ModelError
from qedcode.verify.run import kill_process_tree, run, spawn_detached

log = logging.getLogger("qedcode.server")

HEALTH_TIMEOUT_S = 120.0
HEALTH_POLL_S = 0.5
IDLE_CHECK_S = 30.0
SERVER_LOG_NAME = "llama-server.log"
_OOM_MARKERS = (
    "out of memory",
    "failed to allocate",
    "cudamalloc failed",
    "erroroutofdevicememory",
    "insufficient memory",
    "unable to allocate",
)
_FLAG_RE = re.compile(r"(?<![\w-])(--?[A-Za-z][\w-]*)")


class ServerStartError(ModelError):
    """The model server did not start or did not become healthy."""


class ServerInfo(BaseModel):
    pid: int
    create_time: float = 0.0
    port: int
    model_id: str
    model_path: str
    backend: str = "llamacpp"
    ctx: int
    kv_cache: str = "q8_0"
    engine_tag: str = ""
    started_at: float
    last_used: float

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


@dataclass
class ServerSpec:
    """Everything needed to start (or recognise) the shared server."""

    model_id: str
    model_path: Path
    launcher: list[str]  # [path to llama-server]; tests use [python, fake_server.py]
    ctx: int = 16384
    kv_cache: str = "q8_0"
    ngl: int = 99  # 0 on CPU-only machines
    backend: str = "llamacpp"
    engine_tag: str = ""
    cwd: Path | None = None
    env: dict[str, str] = field(default_factory=dict)
    flags_cache: Path | None = None


@dataclass
class ServerFlags:
    """Which command-line flags this llama-server build understands."""

    names: set[str] = field(default_factory=set)
    flash_attn_takes_value: bool = False

    def has(self, *names: str) -> bool:
        return any(n in self.names for n in names)


# --------------------------------------------------------------------------- small helpers


def free_port() -> int:
    """Ask the OS for an unused local TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def parse_help_flags(text: str) -> ServerFlags:
    """Read the flag names out of `llama-server --help`, and how --flash-attn is spelled."""
    names = set(_FLAG_RE.findall(text))
    takes_value = bool(re.search(r"--flash-attn[^\n]*\[?(on\|off|on\s*\|\s*off|on,|auto)", text))
    return ServerFlags(names=names, flash_attn_takes_value=takes_value)


def probe_flags(
    launcher: list[str],
    cache_path: Path | None = None,
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> ServerFlags:
    """Run `llama-server --help` once per build and cache the flag list next to the build."""
    if cache_path is not None:
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            return ServerFlags(set(data["names"]), bool(data.get("flash_attn_takes_value")))
        except (OSError, KeyError, ValueError):
            pass
    result = run([*launcher, "--help"], timeout=60, env=env, cwd=cwd)
    flags = parse_help_flags(result.stdout + "\n" + result.stderr)
    if cache_path is not None and flags.names:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "names": sorted(flags.names),
                        "flash_attn_takes_value": flags.flash_attn_takes_value,
                    }
                ),
                encoding="utf-8",
            )
        except OSError:
            pass
    return flags


def build_server_args(spec: ServerSpec, port: int, flags: ServerFlags) -> list[str]:
    """The llama-server command line. KV quantisation is used only with flash attention."""
    args = [
        *spec.launcher,
        "-m",
        str(spec.model_path),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "-c",
        str(spec.ctx),
        "-ngl",
        str(spec.ngl),
        "--parallel",
        "1",
    ]
    if flags.has("--jinja"):
        args.append("--jinja")
    if flags.has("--flash-attn", "-fa") and spec.ngl > 0:
        args += ["--flash-attn", "on"] if flags.flash_attn_takes_value else ["--flash-attn"]
        if spec.kv_cache == "q8_0" and flags.has("-ctk", "--cache-type-k"):
            args += ["-ctk", "q8_0", "-ctv", "q8_0"]
    return args


def _process_matches(pid: int, create_time: float) -> bool:
    try:
        proc = psutil.Process(pid)
        if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
            return False
        return create_time == 0.0 or abs(proc.create_time() - create_time) < 1.0
    except psutil.Error:
        return False


def _atomic_write(path: Path, text: str) -> None:
    """Write a file in one step. The temp name is unique per thread, so threads never collide."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    for attempt in range(5):  # Windows refuses a replace while another process is reading
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.02)


class FileLock:
    """A cross-platform lock file; a lock whose owner died (or is very old) is taken over."""

    def __init__(self, path: Path, timeout: float = 60.0, stale_after: float = 180.0) -> None:
        self.path, self.timeout, self.stale_after = path, timeout, stale_after

    def __enter__(self) -> FileLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                self._drop_if_stale()
                if time.monotonic() > deadline:
                    raise ServerStartError(
                        "Another QEDCode process is starting the model server and did not "
                        f"finish in {int(self.timeout)} seconds. Wait a moment and try again; if "
                        f"it keeps happening, delete {self.path}."
                    ) from None
                time.sleep(0.05)
                continue
            with os.fdopen(fd, "w") as handle:
                handle.write(str(os.getpid()))
            return self

    def __exit__(self, *exc: object) -> None:
        self.path.unlink(missing_ok=True)

    def _drop_if_stale(self) -> None:
        try:
            owner = int(self.path.read_text().strip() or 0)
            age = time.time() - self.path.stat().st_mtime
        except (OSError, ValueError):
            return
        if (owner and not psutil.pid_exists(owner)) or age > self.stale_after:
            self.path.unlink(missing_ok=True)


# --------------------------------------------------------------------------- the manager


class ServerManager:
    """Start, reuse and stop the one shared model server for this machine."""

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._idle_stop = threading.Event()

    # ----- paths (read QEDCODE_HOME at call time so tests can redirect it)

    @property
    def state_path(self) -> Path:
        return run_dir() / "server.json"

    @property
    def lock_path(self) -> Path:
        return run_dir() / "server.lock"

    @property
    def used_path(self) -> Path:
        """When a client last used the server. Kept apart from server.json so that recording a
        use never rewrites (and so never clobbers) the record of which server is running."""
        return run_dir() / "server.last_used"

    @property
    def clients_dir(self) -> Path:
        return run_dir() / "clients"

    @property
    def log_path(self) -> Path:
        return logs_dir() / SERVER_LOG_NAME

    # ----- state

    def _read_state(self) -> ServerInfo | None:
        try:
            return ServerInfo.model_validate_json(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _write_state(self, info: ServerInfo) -> None:
        _atomic_write(self.state_path, info.model_dump_json(indent=1))

    def info(self) -> ServerInfo | None:
        """The recorded server if its process is alive (it may still be loading); else None."""
        state = self._read_state()
        if state is None:
            return None
        if not _process_matches(state.pid, state.create_time):
            self.state_path.unlink(missing_ok=True)
            return None
        return state

    def is_healthy(self, info: ServerInfo | None = None) -> bool:
        info = info or self.info()
        if info is None:
            return False
        try:
            return httpx.get(f"{info.base_url}/health", timeout=2.0).status_code == 200
        except httpx.HTTPError:
            return False

    def touch(self) -> None:
        """Record that a client just used the server (resets the idle timer)."""
        if not self.state_path.exists():
            return
        try:
            _atomic_write(self.used_path, repr(self._clock()))
        except OSError:
            log.debug("Could not record server use time", exc_info=True)  # best effort only

    # ----- start / reuse

    def ensure(
        self,
        spec: ServerSpec,
        *,
        on_status: Callable[[str], None] | None = None,
        timeout: float = HEALTH_TIMEOUT_S,
    ) -> ServerInfo:
        """Reuse the live server for this model, or start one. Returns when it is healthy."""
        say = on_status or (lambda _msg: None)
        with FileLock(self.lock_path):
            live = self.info()
            if live is not None and Path(live.model_path) == Path(spec.model_path):
                info = live
            else:
                if live is not None:
                    say("Switching models: stopping the old model server...")
                    self._stop_locked(live)
                info = self._start_locked(spec, say)
        self.register_client()
        self._wait_healthy(info, timeout, say)
        self.touch()
        return self._read_state() or info

    def _start_locked(self, spec: ServerSpec, say: Callable[[str], None]) -> ServerInfo:
        if not Path(spec.model_path).exists():
            raise ServerStartError(
                f"The model file {spec.model_path} does not exist. Download it with "
                f"`qed models download {spec.model_id}` or run `qed setup`."
            )
        say("Loading model...")
        flags = probe_flags(spec.launcher, spec.flags_cache, env=spec.env, cwd=spec.cwd)
        port = free_port()
        args = build_server_args(spec, port, flags)
        rotate_file(self.log_path)
        proc = spawn_detached(args, cwd=spec.cwd, env=spec.env, log_path=self.log_path)
        try:
            create_time = psutil.Process(proc.pid).create_time()
        except psutil.Error:
            create_time = 0.0
        now = self._clock()
        info = ServerInfo(
            pid=proc.pid,
            create_time=create_time,
            port=port,
            model_id=spec.model_id,
            model_path=str(spec.model_path),
            backend=spec.backend,
            ctx=spec.ctx,
            kv_cache=spec.kv_cache,
            engine_tag=spec.engine_tag,
            started_at=now,
            last_used=now,
        )
        self._write_state(info)
        self.used_path.unlink(missing_ok=True)
        log.info("Started llama-server pid %s on port %s for %s", proc.pid, port, spec.model_id)
        return info

    def _wait_healthy(self, info: ServerInfo, timeout: float, say: Callable[[str], None]) -> None:
        deadline = time.monotonic() + timeout
        announced = False
        while time.monotonic() < deadline:
            if not _process_matches(info.pid, info.create_time):
                self.state_path.unlink(missing_ok=True)
                raise ServerStartError(self._explain_exit())
            try:
                status = httpx.get(f"{info.base_url}/health", timeout=2.0).status_code
            except httpx.HTTPError:
                status = 0
            if status == 200:
                return
            if not announced:
                say("Loading model...")
                announced = True
            time.sleep(HEALTH_POLL_S)
        self.stop()
        raise ServerStartError(
            f"The model server did not become ready within {int(timeout)} seconds, so it was "
            f"stopped. The model may be too big for this computer. See {self.log_path}; try a "
            "smaller context (ctx = 8192) or a smaller model (`qed models recommend`)."
        )

    def _explain_exit(self) -> str:
        tail = ""
        try:
            tail = self.log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        except OSError:
            pass
        if any(marker in tail.lower() for marker in _OOM_MARKERS):
            return (
                "The model server stopped because the model did not fit in memory. Lower ctx in "
                "~/.qedcode/config.toml (try 8192), pick a smaller model with "
                f"`qed models recommend`, or close programs that use the GPU. Log: {self.log_path}"
            )
        last = [ln for ln in tail.splitlines() if ln.strip()][-1:] or ["no message"]
        return (
            f"The model server stopped right after starting ({last[0][:200]}). "
            f"See {self.log_path} for details, then run `qed doctor`."
        )

    # ----- stop

    def _stop_locked(self, info: ServerInfo) -> None:
        kill_process_tree(info.pid)
        self.state_path.unlink(missing_ok=True)
        self.used_path.unlink(missing_ok=True)
        log.info("Stopped llama-server pid %s", info.pid)

    def stop(self) -> bool:
        """Stop the QEDCode-owned server if one is running. Returns True if one was stopped."""
        with FileLock(self.lock_path):
            info = self.info()
            if info is None:
                self.state_path.unlink(missing_ok=True)
                return False
            self._stop_locked(info)
            return True

    # ----- idle unload and clients

    def idle_seconds(self) -> float | None:
        state = self._read_state()
        if state is None:
            return None
        last = state.last_used
        try:
            last = max(last, float(self.used_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        return self._clock() - last

    def unload_if_idle(self, keep_alive_minutes: float) -> bool:
        """Stop the server when it has been idle longer than `keep_alive_minutes`."""
        idle = self.idle_seconds()
        if idle is None or idle < keep_alive_minutes * 60:
            return False
        return self.stop()

    def start_idle_watcher(
        self, keep_alive_minutes: float, interval: float = IDLE_CHECK_S
    ) -> threading.Thread:
        """For long-running clients (TUI, MCP): check for idleness every `interval` seconds."""
        self._idle_stop.clear()

        def watch() -> None:
            while not self._idle_stop.wait(interval):
                try:
                    self.unload_if_idle(keep_alive_minutes)
                except Exception:  # noqa: BLE001 - a watcher must never die
                    log.warning("Idle check failed", exc_info=True)

        thread = threading.Thread(target=watch, name="qed-idle-watcher", daemon=True)
        thread.start()
        return thread

    def stop_idle_watcher(self) -> None:
        self._idle_stop.set()

    def register_client(self, kind: str = "client") -> None:
        pid = os.getpid()
        _atomic_write(
            self.clients_dir / f"{pid}.json",
            json.dumps({"pid": pid, "kind": kind, "since": self._clock()}),
        )

    def unregister_client(self) -> None:
        (self.clients_dir / f"{os.getpid()}.json").unlink(missing_ok=True)

    def live_clients(self, *, exclude_self: bool = False) -> list[int]:
        """PIDs of registered clients that are still running (stale registrations are removed)."""
        alive: list[int] = []
        if not self.clients_dir.exists():
            return alive
        for path in self.clients_dir.glob("*.json"):
            try:
                pid = int(path.stem)
            except ValueError:
                continue
            if psutil.pid_exists(pid):
                if not (exclude_self and pid == os.getpid()):
                    alive.append(pid)
            else:
                path.unlink(missing_ok=True)
        return sorted(alive)

    def release(self, *, unload_on_exit: bool = True) -> bool:
        """Unregister this process; stop the server if asked and nobody else is using it."""
        self.unregister_client()
        if unload_on_exit and not self.live_clients():
            return self.stop()
        return False
