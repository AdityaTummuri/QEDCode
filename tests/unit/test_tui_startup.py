from __future__ import annotations

import sys
import time
from pathlib import Path

from qedcode.config import GlobalConfig, load_project, save_global
from qedcode.hardware.detect import HardwareInfo
from qedcode.project.detect import detect_project, first_run
from qedcode.runtime.fake import FakeModel
from qedcode.tui.app import QedApp
from qedcode.vcs.git import has_commits, is_git_repo
from tests.conftest import make_repo, write_files
from tests.tui_helpers import make_app, ready, run_task, screen_name, wait_for
from tests.zoo_helpers import PY, script_for, zoo_git_repo

ZOO = "001_paginate_off_by_one"


async def on_screen(app, name: str) -> None:
    await wait_for(lambda: screen_name(app) == name)


def with_global_config() -> GlobalConfig:
    cfg = GlobalConfig()
    save_global(cfg)
    return cfg


# ------------------------------------------------------------------ no global config: offer setup


async def test_with_no_global_config_it_offers_to_run_setup(tmp_path: Path) -> None:
    ran: list[bool] = []
    app, _, _ = make_app(tmp_path, startup_checks=True, setup_runner=lambda: ran.append(True))
    async with app.run_test(size=(120, 40)) as pilot:
        await on_screen(app, "ConfirmScreen")
        assert app.screen.title_text == "First run" and "has not been set up" in app.screen.prompt
        assert "before downloading anything" in app.screen.prompt
        await pilot.press("y")
        await wait_for(lambda: bool(ran))
        await wait_for(lambda: screen_name(app) != "ConfirmScreen" or app.state == "ready")
        await ready(app)
    assert ran == [True]


async def test_declining_setup_continues_with_the_defaults(tmp_path: Path) -> None:
    ran: list[bool] = []
    app, _, _ = make_app(tmp_path, startup_checks=True, setup_runner=lambda: ran.append(True))
    async with app.run_test(size=(120, 40)) as pilot:
        await on_screen(app, "ConfirmScreen")
        await pilot.press("n")
        await wait_for(lambda: any("Skipping setup" in ln for ln in app.log_lines))
        await wait_for(
            lambda: app.state in ("ready", "loading") and screen_name(app) != "ConfirmScreen"
        )
    assert ran == []


# ------------------------------------------------------------------ a folder that is not a git repo


async def test_a_plain_folder_offers_git_init_and_a_first_commit(tmp_path: Path) -> None:
    folder = tmp_path / "plain"
    write_files(folder, {"a.py": "A = 1\n", "tests/test_a.py": "def test_a():\n    assert True\n"})
    with_global_config()
    app = QedApp(
        folder, config=GlobalConfig(), client_factory=lambda c: FakeModel({}), startup_checks=True
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await on_screen(app, "ConfirmScreen")
        assert "not a git repository" in app.screen.prompt and "git init" in app.screen.prompt
        await pilot.press("y")
        await wait_for(lambda: has_commits(folder) if is_git_repo(folder) else False)
        await on_screen(app, "ConfirmScreen")  # next: the project settings
        await pilot.press("n")
    assert is_git_repo(folder) and has_commits(folder)
    assert any("Created the git repository" in ln for ln in app.log_lines)


async def test_saying_no_to_git_quits_without_changing_anything(tmp_path: Path) -> None:
    folder = tmp_path / "plain"
    write_files(folder, {"a.py": "A = 1\n"})
    with_global_config()
    app = QedApp(
        folder, config=GlobalConfig(), client_factory=lambda c: FakeModel({}), startup_checks=True
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await on_screen(app, "ConfirmScreen")
        await pilot.press("n")
        await wait_for(lambda: not app.is_running, timeout=10)
    assert not is_git_repo(folder)


async def test_a_repo_without_commits_offers_a_first_commit(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", {"a.py": "A = 1\n"}, commit=False)
    with_global_config()
    app = QedApp(
        repo, config=GlobalConfig(), client_factory=lambda c: FakeModel({}), startup_checks=True
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await on_screen(app, "ConfirmScreen")
        assert "no commits yet" in app.screen.prompt
        await pilot.press("y")
        await wait_for(lambda: has_commits(repo))
        await on_screen(app, "ConfirmScreen")
        await pilot.press("n")


# ------------------------------------------------------------------ project settings


def use_this_python(monkeypatch) -> None:
    real = first_run
    monkeypatch.setattr("qedcode.tui.app.first_run", lambda root: real(root, which=lambda name: PY))


async def test_new_project_shows_the_detected_commands_and_saves_them_on_yes(
    tmp_path: Path, monkeypatch
) -> None:
    repo = zoo_git_repo(tmp_path, ZOO, confirmed=False)
    with_global_config()
    use_this_python(monkeypatch)
    app = QedApp(
        repo, config=GlobalConfig(), client_factory=lambda c: FakeModel({}), startup_checks=True
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await on_screen(app, "ConfirmScreen")
        prompt = app.screen.prompt
        assert "found these settings for this project" in prompt
        assert f"Python: {PY}" in prompt and "pytest" in prompt and "5 tests in 1 files" in prompt
        assert "Never edited: tests/**" in prompt
        await pilot.press("y")
        await on_screen(app, "ConfirmScreen")  # offer: add .qed/ to .gitignore
        assert ".gitignore" in app.screen.prompt
        await pilot.press("y")
        await ready(app)
        assert "pytest (5)" in str(app.query_one("#modebar").render())
    saved = load_project(repo)
    assert saved.confirmed and saved.python == PY
    assert ".qed/" in (repo / ".gitignore").read_text()
    assert any("Saved the project settings" in ln for ln in app.log_lines)


async def test_saying_no_uses_the_detected_settings_for_this_session_only(
    tmp_path: Path, monkeypatch
) -> None:
    repo = zoo_git_repo(tmp_path, ZOO, confirmed=False)
    with_global_config()
    use_this_python(monkeypatch)
    app = QedApp(
        repo, config=GlobalConfig(), client_factory=lambda c: FakeModel({}), startup_checks=True
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await on_screen(app, "ConfirmScreen")
        await pilot.press("n")
        await ready(app)
    assert not (repo / ".qed" / "project.toml").exists()
    assert any("this session only" in ln for ln in app.log_lines)


async def test_missing_pytest_is_offered_after_confirming(tmp_path: Path, monkeypatch) -> None:
    repo = zoo_git_repo(tmp_path, ZOO, confirmed=False)
    with_global_config()
    from qedcode.verify.run import RunResult

    def no_pytest_probe(args, cwd):
        return RunResult(args=args, exit_code=0, stdout="3.12.1\nNOPYTEST\n")

    monkeypatch.setattr(
        "qedcode.tui.app.first_run",
        lambda root: first_run(root, which=lambda n: PY, probe=no_pytest_probe),
    )
    installed: list[str] = []

    def fake_install(python, root, confirm):
        installed.append(python)
        assert confirm("ok") is True
        return RunResult(args=[python], exit_code=0)

    monkeypatch.setattr("qedcode.tui.app.install_pytest", fake_install)
    app = QedApp(
        repo, config=GlobalConfig(), client_factory=lambda c: FakeModel({}), startup_checks=True
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await on_screen(app, "ConfirmScreen")
        assert "pytest not installed" in app.screen.prompt
        await pilot.press("y")
        await on_screen(app, "ConfirmScreen")
        assert (
            "pytest is not installed" in app.screen.prompt
            and "Install it there now?" in app.screen.prompt
        )
        await pilot.press("y")
        await wait_for(lambda: bool(installed))
        await on_screen(app, "ConfirmScreen")  # the .gitignore offer
        await pilot.press("n")
        await wait_for(lambda: any("Installed pytest" in ln for ln in app.log_lines))
    assert installed == [PY]


async def test_a_confirmed_project_goes_straight_to_loading(tmp_path: Path) -> None:
    app, _, _ = make_app(tmp_path, startup_checks=True, config=with_global_config())
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        assert screen_name(app) != "ConfirmScreen"
        await run_task(pilot, app)
    assert app.last_result.best.grade.value == "A"


# ------------------------------------------------------------------ the model's life


class LoadingModel(FakeModel):
    """Like the llama.cpp client: a server that takes a moment to start, plus an idle watcher."""

    def __init__(self, script: dict) -> None:
        super().__init__(script, name="llamacpp:fake-4b")
        self.watchers: list[float] = []
        self.watcher_stopped = 0
        self.manager = self

    def ensure_started(self, on_status=None):
        if on_status:
            on_status("Loading model...")
        time.sleep(0.6)

    def start_idle_watcher(self, minutes: float) -> None:
        self.watchers.append(minutes)

    def stop_idle_watcher(self) -> None:
        self.watcher_stopped += 1


async def test_the_model_loads_with_a_loading_line_and_the_idle_watcher_starts(
    tmp_path: Path,
) -> None:
    model = LoadingModel(script_for(ZOO))
    app, _, _ = make_app(tmp_path, model=model)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_for(lambda: app.state == "loading")
        assert "[loading]" in str(app.query_one("#header").render())
        await ready(app)
        assert "Loading model..." in app.log_lines
        assert "llamacpp:fake-4b" in str(app.query_one("#header").render())
        assert model.watchers == [10]  # keep_alive_minutes from the config
        await pilot.pause()


async def test_quitting_unloads_the_model_and_stops_the_watcher(tmp_path: Path) -> None:
    model = LoadingModel(script_for(ZOO))
    app, _, _ = make_app(tmp_path, model=model)
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        await pilot.press("escape")
        await pilot.press("q")
        await wait_for(lambda: not app.is_running, timeout=10)
    assert model.unload_count == 1 and model.watcher_stopped == 1


async def test_quitting_keeps_the_model_when_unload_on_exit_is_off(tmp_path: Path) -> None:
    cfg = GlobalConfig()
    cfg.engine.unload_on_exit = False
    model = FakeModel(script_for(ZOO))
    app, _, _ = make_app(tmp_path, model=model, config=cfg)
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        await pilot.press("escape")
        await pilot.press("q")
        await wait_for(lambda: not app.is_running, timeout=10)
    assert model.unload_count == 0


async def test_quitting_while_a_task_runs_cancels_it_and_unloads(tmp_path: Path) -> None:
    from tests.tui_helpers import SlowModel, submit

    slow = SlowModel(script_for(ZOO))
    slow.delay = 0.6
    app, _, repo = make_app(tmp_path, model=slow)
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        await submit(pilot, "paginate drops the last item")
        await wait_for(lambda: app.state == "busy")
        await pilot.press("escape")
        await pilot.press("q")
        await wait_for(lambda: not app.is_running, timeout=15)
    assert slow.unload_count == 1


# ------------------------------------------------------------------ the header's GPU text


async def test_the_header_shows_gpu_name_and_vram_use(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("qedcode.tui.app.vram_usage", lambda vendor: (3.14, 6.0))
    nvidia = HardwareInfo(vendor="nvidia", gpu_name="RTX 4050", vram_gb=6.0, backend="cuda")
    app, _, _ = make_app(tmp_path, hardware=nvidia)
    async with app.run_test(size=(140, 40)) as pilot:
        await ready(app)
        assert "RTX 4050, 3.1/6.0 GB" in str(app.query_one("#header").render())
        await pilot.pause()


async def test_cpu_only_and_unknown_vram_are_shown_plainly(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("qedcode.tui.app.vram_usage", lambda vendor: None)
    cpu = HardwareInfo(vendor="cpu", backend="cpu")
    app, _, _ = make_app(tmp_path / "a", hardware=cpu)
    async with app.run_test(size=(140, 40)):
        await ready(app)
        assert "CPU only" in str(app.query_one("#header").render())
    apple = HardwareInfo(vendor="apple", gpu_name="Apple M2", vram_gb=4.8, backend="metal")
    app2, _, _ = make_app(tmp_path / "b", hardware=apple)
    async with app2.run_test(size=(140, 40)):
        await ready(app2)
        assert "Apple M2, n/a" in str(app2.query_one("#header").render())


def test_python_is_the_test_runner_for_detection() -> None:
    assert sys.executable == PY and detect_project is not None
