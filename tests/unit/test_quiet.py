"""Quiet mode (AGENTS.md section 15.4)."""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import psutil
import pytest
from typer.testing import CliRunner

from qedcode.cli import app
from qedcode.config import GlobalConfig, load_global
from qedcode.engine import api, doctor
from qedcode.engine.callbacks import NonInteractiveCallbacks
from qedcode.engine.solve import solve
from qedcode.models.schemas import Mode, Speed
from qedcode.runtime.fake import FakeModel
from qedcode.runtime.quiet import (
    QUIET_BATCH,
    QUIET_KEEP_ALIVE_MINUTES,
    QUIET_PAUSE_S,
    QUIET_UBATCH,
    effective_keep_alive,
    is_quiet,
    lower_priority,
    on_battery,
    pause_seconds,
    quiet_threads,
)
from qedcode.runtime.server_manager import ServerFlags, ServerSpec, build_server_args
from tests.zoo_helpers import script_for, zoo_git_repo

ZOO = "001_paginate_off_by_one"


@dataclass
class Battery:
    power_plugged: bool | None


def config(quiet: str = "off", **engine) -> GlobalConfig:
    cfg = GlobalConfig()
    cfg.engine.quiet = quiet  # type: ignore[assignment]
    for key, value in engine.items():
        setattr(cfg.engine, key, value)
    return cfg


def unplugged() -> Battery:
    return Battery(False)


def plugged() -> Battery:
    return Battery(True)


def no_battery() -> None:
    return None


# ------------------------------------------------------------------ the setting


def test_the_default_is_off() -> None:
    assert GlobalConfig().engine.quiet == "off"
    assert not is_quiet(GlobalConfig(), unplugged)


def test_on_off_and_auto() -> None:
    assert is_quiet(config("on"), plugged) and is_quiet(config("on"), no_battery)
    assert not is_quiet(config("off"), unplugged)
    assert is_quiet(config("auto"), unplugged)
    assert not is_quiet(config("auto"), plugged)
    assert not is_quiet(config("auto"), no_battery)  # a desktop has no battery


def test_battery_sensor_problems_never_raise() -> None:
    def broken() -> Battery:
        raise OSError("no sensor")

    assert on_battery(broken) is False and on_battery(lambda: Battery(None)) is False
    assert on_battery(unplugged) is True


def test_threads_are_half_the_cores_but_at_least_one() -> None:
    assert [quiet_threads(n) for n in (1, 2, 3, 8, 16)] == [1, 1, 1, 4, 8]
    assert quiet_threads() >= 1


def test_keep_alive_is_shorter_only_in_quiet_mode() -> None:
    assert effective_keep_alive(config("off", keep_alive_minutes=10), unplugged) == 10
    assert (
        effective_keep_alive(config("on", keep_alive_minutes=10), plugged)
        == QUIET_KEEP_ALIVE_MINUTES
    )
    assert effective_keep_alive(config("on", keep_alive_minutes=1), plugged) == 1  # never longer
    assert effective_keep_alive(config("auto", keep_alive_minutes=10), unplugged) == 2
    assert effective_keep_alive(config("auto", keep_alive_minutes=10), plugged) == 10


def test_the_pause_is_for_local_models_only() -> None:
    assert pause_seconds(config("on"), plugged) == QUIET_PAUSE_S == 3.0
    assert pause_seconds(config("off"), plugged) == 0.0
    cloud = config("on")
    cloud.engine.mode = Mode.CLOUD
    assert pause_seconds(cloud, plugged) == 0.0  # a cloud model does not heat this computer


# ------------------------------------------------------------------ the server


def flags(*names: str) -> ServerFlags:
    return ServerFlags(names=set(names))


def spec(**kw) -> ServerSpec:
    return ServerSpec("m", Path("/m.gguf"), ["llama-server"], **kw)


ALL_FLAGS = (
    "-t",
    "--threads",
    "-tb",
    "--threads-batch",
    "-b",
    "--batch-size",
    "-ub",
    "--ubatch-size",
)


def test_quiet_flags_are_added_when_the_build_knows_them() -> None:
    args = build_server_args(spec(quiet=True, threads=4), 8080, flags(*ALL_FLAGS))
    assert (
        args[args.index("--threads") + 1] == "4" and args[args.index("--threads-batch") + 1] == "4"
    )
    assert args[args.index("--batch-size") + 1] == str(QUIET_BATCH)
    assert args[args.index("--ubatch-size") + 1] == str(QUIET_UBATCH)


def test_unknown_flags_are_never_passed() -> None:
    assert build_server_args(spec(quiet=True, threads=4), 1, flags()) == build_server_args(
        spec(), 1, flags()
    )
    only_batch = build_server_args(spec(quiet=True, threads=4), 1, flags("-b"))
    assert "--batch-size" in only_batch and "--threads" not in only_batch
    assert "--ubatch-size" not in only_batch


def test_a_normal_server_gets_no_quiet_flags() -> None:
    normal = build_server_args(spec(), 8080, flags(*ALL_FLAGS))
    assert "--threads" not in normal and "--batch-size" not in normal
    zero = build_server_args(spec(quiet=True, threads=0), 8080, flags(*ALL_FLAGS))
    assert "--threads" not in zero and "--batch-size" in zero


@pytest.mark.skipif(sys.platform == "win32", reason="nice values are a POSIX idea")
def test_the_priority_of_a_process_is_lowered() -> None:
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        before = psutil.Process(sleeper.pid).nice()
        assert lower_priority(sleeper.pid) is True
        assert psutil.Process(sleeper.pid).nice() >= max(before, 10)
    finally:
        sleeper.kill()
        sleeper.wait()
    assert lower_priority(sleeper.pid) is False  # the process is gone: no exception


@pytest.mark.skipif(sys.platform == "win32", reason="the fake server and nice are POSIX here")
def test_a_quiet_server_runs_at_a_lower_priority(tmp_path: Path) -> None:
    from qedcode.runtime.server_manager import ServerManager
    from tests.conftest import FAKE_SERVER_PATH

    model = tmp_path / "m.gguf"
    model.write_bytes(b"x")
    manager = ServerManager()
    try:
        info = manager.ensure(
            ServerSpec(
                "tiny",
                model,
                [sys.executable, str(FAKE_SERVER_PATH)],
                ctx=2048,
                ngl=0,
                quiet=True,
                threads=2,
            )
        )
        assert psutil.Process(info.pid).nice() >= 10
    finally:
        manager.stop()
    deadline = time.monotonic() + 5
    while manager.info() is not None and time.monotonic() < deadline:
        time.sleep(0.1)


def test_the_llamacpp_client_spec_follows_the_setting(monkeypatch) -> None:
    from qedcode.hardware.detect import HardwareInfo
    from qedcode.runtime import installer, llamacpp

    hw = HardwareInfo(vendor="nvidia", gpu_name="x", vram_gb=6, backend="cuda", source="test")
    engine = installer.InstalledEngine("b1", "cuda", Path("/e"), Path("/e/llama-server"))
    monkeypatch.setattr("qedcode.hardware.detect.current_hardware", lambda *a, **k: hw)
    monkeypatch.setattr(installer, "find_installed_engine", lambda hw_, tag: engine)
    monkeypatch.setattr(llamacpp, "resolve_model_path", lambda model: ("m", Path("/m.gguf")))
    quiet = llamacpp.make_llamacpp_client(config("on"))
    assert quiet.spec.quiet is True and quiet.spec.threads == quiet_threads()
    normal = llamacpp.make_llamacpp_client(config("off"))
    assert normal.spec.quiet is False and normal.spec.threads == 0


# ------------------------------------------------------------------ the controller


def run_solve(repo: Path, script: dict, **kw):
    sleeps: list[float] = []
    result = solve(
        repo, "paginate drops the last item", Speed.NORMAL, Mode.LOCAL, NonInteractiveCallbacks(),
        client=FakeModel(script), sleep=sleeps.append, **kw,
    )  # fmt: skip
    return result, sleeps


def two_tries() -> dict:
    junk = "PLAN:\n- nothing\n"
    good = script_for(ZOO)["coder"][0]
    return script_for(ZOO, coder=[junk, junk, good])  # the first try needs a retry and fails


def test_no_pause_without_quiet_mode(tmp_path: Path) -> None:
    result, sleeps = run_solve(zoo_git_repo(tmp_path), two_tries())
    assert result.status == "done" and sleeps == []


def test_a_pause_between_tries_but_never_before_the_first(tmp_path: Path) -> None:
    events: list[str] = []

    class Spy(NonInteractiveCallbacks):
        def on_event(self, event) -> None:
            events.append(event.message)

    repo = zoo_git_repo(tmp_path)
    sleeps: list[float] = []
    result = solve(
        repo, "paginate drops the last item", Speed.NORMAL, Mode.LOCAL, Spy(),
        client=FakeModel(two_tries()), pause_s=3.0, sleep=sleeps.append,
    )  # fmt: skip
    assert result.status == "done" and result.best is not None and result.best.id == "c2"
    assert sum(sleeps) == pytest.approx(3.0) and all(s <= 0.1 + 1e-9 for s in sleeps)
    pauses = [m for m in events if m.startswith("Quiet mode: pausing")]
    assert pauses == ["Quiet mode: pausing 3 seconds to keep the computer cool."]
    first_try = next(i for i, m in enumerate(events) if m.startswith("Try 1"))
    assert not any(m.startswith("Quiet mode") for m in events[:first_try])


def test_a_cancel_ends_the_pause_at_once(tmp_path: Path) -> None:
    cancelled = {"now": False}

    class Cancelling(NonInteractiveCallbacks):
        def cancelled(self) -> bool:
            return cancelled["now"]

    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        cancelled["now"] = True  # the user presses Esc during the first wait

    repo = zoo_git_repo(tmp_path)
    result = solve(
        repo, "paginate drops the last item", Speed.NORMAL, Mode.LOCAL, Cancelling(),
        client=FakeModel(two_tries()), pause_s=3.0, sleep=sleep,
    )  # fmt: skip
    assert len(sleeps) == 1 and result.status == "cancelled"


def test_the_api_passes_the_pause_from_the_config(tmp_path: Path, monkeypatch) -> None:
    seen: dict = {}

    def capture(*args, **kwargs):
        seen.update(kwargs)
        return api.SolveResult(
            status="no_fix", task=api.TaskSpec(id="t", request="x", speed=Speed.NORMAL)
        )

    monkeypatch.setattr(api, "_solve", capture)
    repo = zoo_git_repo(tmp_path)
    api.solve(repo, "task", config=config("on"), client=FakeModel({}))
    assert seen["pause_s"] == 3.0
    api.solve(repo, "task", config=config("off"), client=FakeModel({}))
    assert seen["pause_s"] == 0.0
    cloud = config("on")
    cloud.engine.mode = Mode.CLOUD
    api.solve(repo, "task", config=cloud, client=FakeModel({}))
    assert seen["pause_s"] == 0.0


# ------------------------------------------------------------------ the command and the doctor


def test_qed_quiet_shows_and_sets_the_mode() -> None:
    runner = CliRunner()
    assert "Quiet mode is off." in runner.invoke(app, ["quiet"]).output
    on = runner.invoke(app, ["quiet", "on"])
    assert on.exit_code == 0 and "half the CPU threads" in on.output and "only slower" in on.output
    assert load_global().engine.quiet == "on"
    assert "Quiet mode is on." in runner.invoke(app, ["quiet"]).output
    assert "switch on by itself" in runner.invoke(app, ["quiet", "auto"]).output
    assert load_global().engine.quiet == "auto"
    assert "full speed" in runner.invoke(app, ["quiet", "off"]).output
    assert load_global().engine.quiet == "off"
    bad = runner.invoke(app, ["quiet", "loud"])
    assert bad.exit_code == 2 and "Use `qed quiet on`" in bad.output
    assert load_global().engine.quiet == "off"


def test_the_doctor_row_for_quiet_mode(monkeypatch) -> None:
    assert doctor.check_quiet(config("off")).status == "ok"
    assert "qed quiet on" in doctor.check_quiet(config("off")).detail
    monkeypatch.setattr("qedcode.runtime.quiet.on_battery", lambda *a, **k: True)
    on = doctor.check_quiet(config("on"))
    assert on.status == "ok" and "active now" in on.detail
    auto = doctor.check_quiet(config("auto"))
    assert "auto (waiting for battery power)" in auto.detail or "auto (active now)" in auto.detail
