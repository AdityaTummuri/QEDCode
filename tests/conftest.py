"""Shared fixtures. Tests must never touch the real home folder."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BUGZOO_DIR = REPO_ROOT / "bugzoo"
FAKE_SERVER_PATH = REPO_ROOT / "tests" / "fixtures" / "fake_llama_server.py"


def zoo_project_names() -> list[str]:
    """Names of the bug zoo projects (NNN_name folders)."""
    return sorted(p.name for p in BUGZOO_DIR.iterdir() if p.is_dir() and p.name[:3].isdigit())


@pytest.fixture(scope="session")
def check_zoo():
    """The bugzoo/check_zoo.py module, loaded from its file path."""
    spec = importlib.util.spec_from_file_location("check_zoo", BUGZOO_DIR / "check_zoo.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["check_zoo"] = module  # dataclasses need the module to be registered
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def zoo_copy(tmp_path: Path):
    """Return a function that copies a bug zoo project into tmp_path and returns the copy."""

    def _copy(name: str) -> Path:
        dest = tmp_path / "zoo" / name
        shutil.copytree(
            BUGZOO_DIR / name, dest, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache")
        )
        return dest

    return _copy


@pytest.fixture(autouse=True)
def _isolated_qedcode_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch) -> Path:
    """Point QEDCODE_HOME at a fresh temp folder for every test."""
    home = tmp_path_factory.mktemp("qedcode_home")
    monkeypatch.setenv("QEDCODE_HOME", str(home))
    return home


@pytest.fixture(autouse=True)
def _hermetic_git(monkeypatch) -> None:
    """Tests never read the developer's git config (signing, hooks, odd settings)."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.com")


def run_git(repo: Path, *args: str, check: bool = True) -> str:
    """Run git in `repo` for test setup and checks; returns stdout."""
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", check=False
    )
    if check and proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc.stdout


def make_repo(
    path: Path, files: dict[str, str | bytes] | None = None, *, commit: bool = True
) -> Path:
    """Create a git repo at `path` with `files` (committed unless commit=False)."""
    path.mkdir(parents=True, exist_ok=True)
    run_git(path, "init", "-q", "-b", "main")
    write_files(path, files or {})
    if commit and files:
        run_git(path, "add", "-A")
        run_git(path, "commit", "-q", "-m", "initial")
    return path


def write_files(root: Path, files: dict[str, str | bytes]) -> None:
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_bytes(content.encode("utf-8"))


def tree_state(root: Path) -> dict[str, bytes]:
    """Every file under `root` (except .git and .qed) with its bytes, for before/after checks."""
    state: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if rel.startswith((".git/", ".qed/")) or rel in (".git", ".qed") or not path.is_file():
            continue
        state[rel] = path.read_bytes()
    return state


@pytest.fixture(autouse=True)
def _reset_qedcode_logging():
    """Remove file handlers that a test's setup_logging() added to the shared logger."""
    yield
    import logging

    logger = logging.getLogger("qedcode")
    for handler in list(logger.handlers):
        if getattr(handler, "_qed_file", False):
            logger.removeHandler(handler)
            handler.close()
    logger.propagate = True


def _make_memory_keyring():
    from keyring.backend import KeyringBackend
    from keyring.errors import PasswordDeleteError

    class _MemoryKeyring(KeyringBackend):
        priority = 1

        def __init__(self) -> None:
            self.store: dict[tuple[str, str], str] = {}

        def set_password(self, service, username, password):
            self.store[(service, username)] = password

        def get_password(self, service, username):
            return self.store.get((service, username))

        def delete_password(self, service, username):
            if (service, username) not in self.store:
                raise PasswordDeleteError("not stored")
            del self.store[(service, username)]

    return _MemoryKeyring()


@pytest.fixture
def memory_keyring():
    """An in-memory system keychain, so tests never touch the real one."""
    import keyring

    previous = keyring.get_keyring()
    backend = _make_memory_keyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(previous)
