"""Shared fixtures. Tests must never touch the real home folder."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_qedcode_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch) -> Path:
    """Point QEDCODE_HOME at a fresh temp folder for every test."""
    home = tmp_path_factory.mktemp("qedcode_home")
    monkeypatch.setenv("QEDCODE_HOME", str(home))
    return home
