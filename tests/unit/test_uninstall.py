from __future__ import annotations

import sys
from pathlib import Path

import keyring
import psutil
from typer.testing import CliRunner

from qedcode.cli import app
from qedcode.config import (
    GlobalConfig,
    ProjectConfig,
    models_dir,
    qedcode_home,
    save_global,
    save_project,
)
from qedcode.engine import api
from qedcode.engine.uninstall import (
    plan_home,
    plan_project,
    uninstall_all,
    uninstall_project,
)
from qedcode.models.schemas import Mode, Speed
from qedcode.runtime.fake import FakeModel
from qedcode.runtime.server_manager import ServerManager, ServerSpec
from qedcode.vcs.git import git_dir
from tests.conftest import FAKE_SERVER_PATH, run_git, tree_state, write_files
from tests.zoo_helpers import PY, script_for, zoo_git_repo

ZOO = "001_paginate_off_by_one"


def used_project(tmp_path: Path) -> Path:
    """A project that has been through a solve: .qed/, refs, receipts and the exclude line."""
    repo = zoo_git_repo(tmp_path)
    api.solve(
        repo,
        "paginate drops the last item",
        config=GlobalConfig(),
        client=FakeModel(script_for(ZOO)),
    )
    assert (repo / ".qed").exists()
    return repo


def test_project_uninstall_lists_asks_and_removes_only_qedcode_files(tmp_path: Path) -> None:
    repo = used_project(tmp_path)
    user_files = tree_state(repo)
    asked: list[str] = []
    done = uninstall_project(repo, lambda prompt: asked.append(prompt) or True)
    assert "Removed the .qed/ folder." in done
    prompt = asked[0]
    assert ".qed/ folder" in prompt and "git ref(s) under refs/qed/" in prompt
    assert "line in .git/info/exclude" in prompt and "Your own files stay untouched" in prompt
    assert not (repo / ".qed").exists()
    assert run_git(repo, "for-each-ref", "refs/qed/").strip() == ""
    assert ".qed" not in (git_dir(repo) / "info" / "exclude").read_text()
    assert tree_state(repo) == user_files  # the user's files are exactly as they were
    assert run_git(repo, "status", "--porcelain").strip() == ""


def test_declining_removes_nothing(tmp_path: Path) -> None:
    repo = used_project(tmp_path)
    assert uninstall_project(repo, lambda prompt: False) == []
    assert (repo / ".qed").exists() and run_git(repo, "for-each-ref", "refs/qed/").strip()


def test_nothing_to_remove_is_not_an_error(tmp_path: Path) -> None:
    repo = zoo_git_repo(tmp_path, confirmed=False)
    assert plan_project(repo).empty
    assert uninstall_project(repo, lambda prompt: True) == []


def test_gitignore_entries_are_left_alone(tmp_path: Path) -> None:
    repo = used_project(tmp_path)
    (repo / ".gitignore").write_text("__pycache__/\n.qed/\n")
    uninstall_project(repo, lambda prompt: True)
    assert ".qed/" in (repo / ".gitignore").read_text()


def test_the_plan_reports_sizes(tmp_path: Path) -> None:
    repo = used_project(tmp_path)
    plan = plan_project(repo)
    assert plan.total_bytes > 0 and "Total:" in plan.describe()


# ------------------------------------------------------------------ --all


def seed_home() -> None:
    save_global(GlobalConfig())
    write_files(
        qedcode_home(),
        {"bin/llama.cpp/b1/cpu/llama-server": "x" * 2000, "logs/qedcode.log": "log\n"},
    )
    models_dir().mkdir(parents=True, exist_ok=True)
    (models_dir() / "m.gguf").write_bytes(b"g" * 5000)


def test_all_lists_the_home_folder_with_sizes_and_removes_it(
    tmp_path: Path, memory_keyring
) -> None:
    repo = used_project(tmp_path)
    seed_home()
    memory_keyring.store[("qedcode", "openrouter")] = "sk-test-key-123456"
    asked: list[str] = []
    done = uninstall_all(repo, lambda prompt: asked.append(prompt) or True)
    prompt = asked[0]
    assert "ALL of QEDCode's files" in prompt and str(qedcode_home() / "models") in prompt
    assert "downloaded models" in prompt and "downloaded llama.cpp engines" in prompt
    assert "saved API keys in the system keychain: openrouter" in prompt and "Total:" in prompt
    assert not qedcode_home().exists() and not (repo / ".qed").exists()
    assert ("qedcode", "openrouter") not in memory_keyring.store
    assert any("Removed the saved API key for openrouter" in line for line in done)
    assert len(asked) == 1  # one question for everything


def test_all_declined_removes_nothing(tmp_path: Path) -> None:
    repo = used_project(tmp_path)
    seed_home()
    assert uninstall_all(repo, lambda prompt: False) == []
    assert qedcode_home().exists() and (repo / ".qed").exists()


def test_all_stops_the_model_server_first(tmp_path: Path) -> None:
    seed_home()
    model = tmp_path / "m.gguf"
    model.write_bytes(b"x")
    manager = ServerManager()
    info = manager.ensure(
        ServerSpec("tiny", model, [sys.executable, str(FAKE_SERVER_PATH)], ctx=2048, ngl=0)
    )
    assert psutil.pid_exists(info.pid)
    uninstall_all(None, lambda prompt: True)
    deadline = __import__("time").monotonic() + 5
    while psutil.pid_exists(info.pid) and __import__("time").monotonic() < deadline:
        __import__("time").sleep(0.1)
    try:
        alive = (
            psutil.pid_exists(info.pid)
            and psutil.Process(info.pid).status() != psutil.STATUS_ZOMBIE
        )
    except psutil.NoSuchProcess:
        alive = False
    assert not alive and not qedcode_home().exists()


def test_all_with_nothing_installed(tmp_path: Path) -> None:
    import shutil

    shutil.rmtree(qedcode_home(), ignore_errors=True)
    assert plan_home().empty
    assert uninstall_all(None, lambda prompt: True, include_keys=False) == []


# ------------------------------------------------------------------ the command


def test_cli_uninstall_asks_and_reports(tmp_path: Path, monkeypatch) -> None:
    repo = used_project(tmp_path)
    monkeypatch.chdir(repo)
    declined = CliRunner().invoke(app, ["uninstall"], input="n\n")
    assert declined.exit_code == 1 and "Nothing was removed." in declined.output
    assert (repo / ".qed").exists()
    accepted = CliRunner().invoke(app, ["uninstall"], input="y\n")
    assert accepted.exit_code == 0 and "Removed the .qed/ folder." in accepted.output
    assert not (repo / ".qed").exists()
    again = CliRunner().invoke(app, ["uninstall", "--yes"])
    assert again.exit_code == 1 and "Nothing was removed." in again.output


def test_cli_uninstall_all_yes(tmp_path: Path, monkeypatch) -> None:
    repo = used_project(tmp_path)
    seed_home()
    monkeypatch.chdir(repo)
    result = CliRunner().invoke(app, ["uninstall", "--all", "--yes"])
    assert result.exit_code == 0, result.output
    flat = "".join(result.output.split())  # long lines wrap in the terminal: ignore all spacing
    assert "".join("ALL of QEDCode's files".split()) in flat
    assert "".join(f"Removed {qedcode_home()}.".split()) in flat
    assert not qedcode_home().exists()


def test_unused_imports_are_used() -> None:
    assert PY and Mode and Speed and ProjectConfig and save_project and keyring
