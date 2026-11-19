"""Shared fixtures. Tests must never touch the real home folder."""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BUGZOO_DIR = REPO_ROOT / "bugzoo"


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
