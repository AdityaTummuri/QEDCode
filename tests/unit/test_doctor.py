from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qedcode.cli import app
from qedcode.config import GlobalConfig, ProjectConfig, models_dir, save_global, save_project
from qedcode.engine import doctor
from qedcode.engine.doctor import (
    Check,
    check_disk,
    check_engine,
    check_git,
    check_hardware,
    check_leftovers,
    check_logs,
    check_model,
    check_project,
    check_python,
    check_ruff,
    check_server,
    run_doctor,
    summarize,
    update_engine,
)
from qedcode.engine.solve import prune_old_runs
from qedcode.hardware.detect import HardwareInfo
from qedcode.models.schemas import Mode
from qedcode.runtime import installer
from qedcode.runtime.catalog import load_catalog, local_path
from qedcode.runtime.installer import EnginePlan, InstalledEngine
from qedcode.vcs import worktree
from qedcode.vcs.snapshot import snapshot
from tests.conftest import make_repo
from tests.zoo_helpers import PY, zoo_git_repo

NVIDIA = HardwareInfo(
    vendor="nvidia", gpu_name="RTX 4050", vram_gb=6.0, backend="cuda", source="nvidia-smi"
)
CPU = HardwareInfo(vendor="cpu", backend="cpu", source="fallback")


def statuses(checks: list[Check]) -> dict[str, str]:
    return {c.name: c.status for c in checks}


# ------------------------------------------------------------------ one check at a time


def test_git_is_found() -> None:
    check = check_git()
    assert check.status == "ok" and check.detail.startswith("git version")


def test_project_checks(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    bad = check_project(plain)
    assert bad.status == "fail" and "not a git repository" in bad.detail and "git init" in bad.fix
    empty = make_repo(tmp_path / "empty", {"a.py": "A = 1\n"}, commit=False)
    assert "no commits" in check_project(empty).detail
    repo = zoo_git_repo(tmp_path / "z", confirmed=False)
    unconfirmed = check_project(repo)
    assert unconfirmed.status == "warn" and "never confirmed" in unconfirmed.detail
    save_project(repo, ProjectConfig(python=PY, confirmed=True))
    assert check_project(repo).status == "ok"


def test_python_and_pytest_checks(tmp_path: Path) -> None:
    repo = zoo_git_repo(tmp_path)  # confirmed project config with this Python
    checks = check_python(repo)
    assert statuses(checks) == {"python": "ok", "pytest": "ok"}
    assert "Python 3." in checks[0].detail and "pytest" in checks[1].detail
    no_pytest = check_python(repo, probe=lambda python, root: ("3.12.1", ""))
    assert no_pytest[1].status == "fail" and "-m pip install pytest" in no_pytest[1].fix
    dead = check_python(repo, probe=lambda python, root: ("", ""))
    assert statuses(dead) == {"python": "fail", "pytest": "fail"} and "uv sync" in dead[0].fix


def test_ruff_check() -> None:
    assert check_ruff().status == "ok"


def test_ruff_missing_is_a_warning(monkeypatch) -> None:
    from qedcode.verify.run import RunResult

    monkeypatch.setattr(doctor, "run", lambda *a, **k: RunResult(args=[], error="not found"))
    check = check_ruff()
    assert check.status == "warn" and "Reinstall QEDCode" in check.fix


def test_hardware_check() -> None:
    assert check_hardware(NVIDIA).status == "ok" and "6.0 GB" in check_hardware(NVIDIA).detail
    cpu = check_hardware(CPU)
    assert cpu.status == "warn" and "slow" in cpu.detail and "set its vendor and VRAM" in cpu.fix


def fake_engine(tmp_path: Path, backend: str = "cuda") -> InstalledEngine:
    from qedcode.runtime.installer import ENGINE_MANIFEST, engine_dir

    folder = engine_dir(installer.DEFAULT_LLAMACPP_TAG, backend)
    folder.mkdir(parents=True)
    name = "llama-server.exe" if sys.platform == "win32" else "llama-server"
    (folder / name).write_text("fake")
    import json

    (folder / ENGINE_MANIFEST).write_text(json.dumps({"binary": name, "version": "v"}))
    return InstalledEngine(installer.DEFAULT_LLAMACPP_TAG, backend, folder, folder / name, "v")


def test_engine_check(tmp_path: Path, monkeypatch) -> None:
    cfg = GlobalConfig()
    missing = check_engine(cfg, NVIDIA)
    assert (
        missing.status == "fail"
        and "is not installed" in missing.detail
        and "qed setup" in missing.fix
    )
    from qedcode.hardware.detect import sys_platform_name

    backend = installer.backend_order(NVIDIA, sys_platform_name())[0]  # metal on a Mac
    fake_engine(tmp_path, backend)
    from qedcode.verify.run import RunResult

    monkeypatch.setattr(
        doctor, "run", lambda args, **k: RunResult(args=[], exit_code=0, stderr="version: 1 (b)")
    )
    ok = check_engine(cfg, NVIDIA)
    assert ok.status == "ok" and f"{backend} build" in ok.detail
    monkeypatch.setattr(
        doctor,
        "run",
        lambda args, **k: RunResult(args=[], exit_code=1, stderr="cuda driver too old"),
    )
    broken = check_engine(cfg, NVIDIA)
    assert (
        broken.status == "fail"
        and "would not start" in broken.detail
        and "--update-engine" in broken.fix
    )


def test_engine_not_needed_for_other_backends() -> None:
    cfg = GlobalConfig()
    cfg.local.backend = "ollama"
    assert "not needed" in check_engine(cfg, NVIDIA).detail
    cfg2 = GlobalConfig()
    cfg2.engine.mode = Mode.CLOUD
    assert check_engine(cfg2, NVIDIA).status == "ok"


def test_model_file_checks(tmp_path: Path) -> None:
    cfg = GlobalConfig()
    cfg.local.model = "qwen3.5-4b-q4km"
    missing = check_model(cfg)
    assert missing.status == "fail" and "is not downloaded" in missing.detail
    assert "qed models download qwen3.5-4b-q4km" in missing.fix
    entry = load_catalog().require("qwen3.5-4b-q4km")
    path = local_path(entry)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"short")
    wrong = check_model(cfg)
    assert (
        wrong.status == "fail" and "wrong size" in wrong.detail and "qed models remove" in wrong.fix
    )
    with path.open("wb") as handle:  # a sparse file of exactly the catalog size
        handle.truncate(entry.gguf.size_bytes)
    assert check_model(cfg).status == "ok"
    cfg.local.model = "no-such-model"
    assert "not a model QEDCode knows" in check_model(cfg).detail
    gguf = tmp_path / "mine.gguf"
    cfg.local.model = str(gguf)
    assert check_model(cfg).status == "fail"
    gguf.write_bytes(b"x")
    assert check_model(cfg).status == "ok"


def test_other_model_backends(monkeypatch) -> None:
    cfg = GlobalConfig()
    cfg.local.backend = "openai_compat"
    assert "no base_url" in check_model(cfg).detail
    cfg.local.base_url = "http://127.0.0.1:9/v1"
    unreachable = check_model(cfg)
    assert unreachable.status == "fail" and "nothing answers" in unreachable.detail
    cfg2 = GlobalConfig()
    cfg2.local.backend = "ollama"
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:9")
    assert (
        check_model(cfg2).status == "fail" and "Ollama is not running" in check_model(cfg2).detail
    )
    cloud = GlobalConfig()
    cloud.engine.mode = Mode.CLOUD
    assert "no cloud model" in check_model(cloud).detail
    cloud.cloud.model = "openai/gpt-x"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("qedcode.runtime.secrets.get_api_key", lambda provider: None)
    no_key = check_model(cloud)
    assert no_key.status == "fail" and "OPENAI_API_KEY" in no_key.fix
    monkeypatch.setattr("qedcode.runtime.secrets.get_api_key", lambda provider: "k")
    assert check_model(cloud).status == "ok"


def test_server_check(tmp_path: Path) -> None:
    import json
    import time

    from qedcode.config import run_dir

    assert "not running" in check_server(GlobalConfig()).detail
    assert (
        "not used"
        in check_server(
            GlobalConfig().model_copy(
                update={"engine": GlobalConfig().engine.model_copy(update={"mode": Mode.CLOUD})}
            )
        ).detail
    )
    # a recorded server whose process is alive but does not answer
    import subprocess

    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        import psutil

        run_dir().mkdir(parents=True, exist_ok=True)
        (run_dir() / "server.json").write_text(
            json.dumps(
                {
                    "pid": sleeper.pid,
                    "create_time": psutil.Process(sleeper.pid).create_time(),
                    "port": 9,
                    "model_id": "m",
                    "model_path": "/x",
                    "ctx": 1,
                    "started_at": time.time(),
                    "last_used": time.time(),
                }
            )
        )
        stuck = check_server(GlobalConfig())
        assert (
            stuck.status == "warn"
            and "not answering" in stuck.detail
            and "still be loading" in stuck.fix
        )
    finally:
        sleeper.kill()
        sleeper.wait()


def test_disk_space_check(monkeypatch) -> None:
    cfg = GlobalConfig()
    cfg.local.model = "qwen3.5-4b-q4km"
    GiB = 1024**3
    monkeypatch.setattr(
        shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(100 * GiB, 10 * GiB, 90 * GiB)
    )
    assert check_disk(cfg).status == "ok"
    monkeypatch.setattr(
        shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(100 * GiB, 99 * GiB, 1 * GiB)
    )
    short = check_disk(cfg)
    assert (
        short.status == "fail"
        and "needs about 2.8 GB" in short.detail
        and "QEDCODE_HOME" in short.fix
    )
    cfg.local.backend = "ollama"
    assert check_disk(cfg).status == "warn"  # no model to download, but under 2 GB free


def test_leftover_worktrees_and_logs(tmp_path: Path) -> None:
    repo = zoo_git_repo(tmp_path)
    assert check_leftovers(repo).status == "ok"
    worktree.create(repo, snapshot(repo, "t-x"), "stale")
    left = check_leftovers(repo)
    assert left.status == "warn" and "git worktree prune" in left.fix
    worktree.remove_all(repo)
    assert check_logs().status == "ok"


# ------------------------------------------------------------------ the whole doctor


def test_run_doctor_covers_every_item_of_the_plan(tmp_path: Path) -> None:
    repo = zoo_git_repo(tmp_path)
    checks = run_doctor(repo, GlobalConfig(), hw=NVIDIA)
    names = [c.name for c in checks]
    assert names == [
        "git",
        "project",
        "python",
        "pytest",
        "ruff",
        "hardware",
        "engine build",
        "model file",
        "model server",
        "disk space",
        "sealed test box",
        "leftover copies",
        "logs",
    ]
    result = statuses(checks)
    assert result["git"] == result["project"] == result["python"] == result["pytest"] == "ok"
    assert result["engine build"] == "fail"  # nothing is installed in the test home
    for check in checks:
        if check.status != "ok":
            assert check.fix, f"{check.name} has no fix"
    ok, warnings, failures = summarize(checks)
    assert ok + warnings + failures == len(checks) and failures >= 1


def test_a_crashing_check_never_crashes_the_doctor(tmp_path: Path, monkeypatch) -> None:
    def boom() -> Check:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(doctor, "check_git", boom)
    repo = zoo_git_repo(tmp_path)
    checks = run_doctor(repo, GlobalConfig(), hw=NVIDIA)
    crashed = next(c for c in checks if c.name == "doctor")
    assert crashed.status == "warn" and "RuntimeError" in crashed.detail
    assert any(c.name == "logs" for c in checks)  # the rest still ran


# ------------------------------------------------------------------ the command


def test_qed_doctor_command_prints_a_table_with_fixes_and_exits_1_on_problems(
    tmp_path: Path, monkeypatch
) -> None:
    repo = zoo_git_repo(tmp_path)
    monkeypatch.chdir(repo)
    save_global(GlobalConfig())
    monkeypatch.setattr("qedcode.hardware.detect.current_hardware", lambda *a, **k: NVIDIA)
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 1, result.output
    out = result.output
    assert "QEDCode doctor" in out and "engine build" in out and "FAIL" in out
    assert "engine build: Run `qed setup`" in out  # the fix is printed under the table
    assert "problem(s)." in out


def test_update_engine_asks_then_installs_and_records_the_tag(monkeypatch) -> None:
    cfg = GlobalConfig()
    plan = EnginePlan("b99999", "vulkan", [], "test")
    installed: list[object] = []

    def download(plans, confirm, progress=None):
        installed.append(confirm("Download the vulkan build?"))
        return InstalledEngine("b99999", "vulkan", Path("/x"), Path("/x/ls"))

    message = update_engine(
        cfg,
        NVIDIA,
        lambda prompt: True,
        fetch_newest=lambda: "b99999",
        fetch_release=lambda tag: {"tag_name": tag, "assets": []},
        download=download,
    )
    monkeypatch.setattr(installer, "find_llamacpp_assets", lambda hw, release: [plan])
    message = update_engine(
        cfg,
        NVIDIA,
        lambda prompt: True,
        fetch_newest=lambda: "b99999",
        fetch_release=lambda tag: {"tag_name": tag, "assets": []},
        download=download,
    )
    assert message.startswith("Updated llama.cpp from b12549 to b99999")
    assert cfg.local.llamacpp_tag == "b99999" and installed == [True]


def test_update_engine_when_already_current_or_nothing_matches(monkeypatch) -> None:
    cfg = GlobalConfig()
    cfg.local.llamacpp_tag = "b100"
    monkeypatch.setattr(installer, "find_installed_engine", lambda hw, tag: object())
    same = update_engine(cfg, NVIDIA, lambda p: True, fetch_newest=lambda: "b100")
    assert "already the newest" in same
    monkeypatch.setattr(installer, "find_installed_engine", lambda hw, tag: None)
    monkeypatch.setattr(installer, "find_llamacpp_assets", lambda hw, release: [])
    none = update_engine(
        cfg,
        NVIDIA,
        lambda p: True,
        fetch_newest=lambda: "b200",
        fetch_release=lambda tag: {"tag_name": tag, "assets": []},
    )
    assert (
        "has no build for this computer; staying on b100" in none
        and cfg.local.llamacpp_tag == "b100"
    )


# ------------------------------------------------------------------ logs and run folders


def test_old_run_folders_are_pruned(tmp_path: Path) -> None:
    runs = tmp_path / ".qed" / "runs"
    for i in range(25):
        folder = runs / f"t-20261120-1200{i:02d}-aaaa"
        folder.mkdir(parents=True)
        (folder / "x.txt").write_text("x")
    assert prune_old_runs(tmp_path, keep=20) == 5
    left = sorted(p.name for p in runs.iterdir())
    assert len(left) == 20 and left[0] == "t-20261120-120005-aaaa"
    assert prune_old_runs(tmp_path / "nothing", keep=20) == 0


def test_log_rotation_keeps_five_megabytes_times_three(tmp_path: Path) -> None:
    from qedcode.logging_setup import KEEP_LOGS, MAX_LOG_BYTES, rotate_file

    assert (MAX_LOG_BYTES, KEEP_LOGS) == (5 * 1024 * 1024, 3)
    log = tmp_path / "llama-server.log"
    for generation in range(5):
        log.write_bytes(b"x" * MAX_LOG_BYTES)
        (tmp_path / "marker").write_text(str(generation))
        assert rotate_file(log) is True
    assert sorted(p.name for p in tmp_path.glob("llama-server.log*")) == [
        "llama-server.log.1",
        "llama-server.log.2",
        "llama-server.log.3",
    ]
    log.write_bytes(b"small")
    assert rotate_file(log) is False  # under the limit: untouched


@pytest.mark.parametrize("name", ["models"])
def test_models_dir_is_under_the_isolated_home(name: str, _isolated_qedcode_home: Path) -> None:
    assert models_dir().is_relative_to(_isolated_qedcode_home)
