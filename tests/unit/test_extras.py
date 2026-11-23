"""The light install (AGENTS.md 15.16): optional parts are lazy and missing ones are explained."""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from qedcode.extras import MissingExtra, load_extra

ROOT = Path(__file__).resolve().parents[2]
OPTIONAL = ("litellm", "huggingface_hub", "textual")


def test_a_missing_extra_names_the_command() -> None:
    with pytest.raises(MissingExtra, match=r"qedcode\[cloud\]"):
        load_extra("surely_not_installed_xyz", "cloud", "Cloud models")


def test_a_broken_module_is_not_reported_as_missing(tmp_path, monkeypatch) -> None:
    (tmp_path / "broken_extra_mod.py").write_text("import surely_not_installed_xyz\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(ModuleNotFoundError):
        load_extra("broken_extra_mod", "cloud", "Cloud models")


def test_the_optional_parts_are_not_default_dependencies() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    default = " ".join(project["dependencies"])
    for name in ("litellm", "huggingface-hub", "textual"):
        assert name not in default
    assert set(project["optional-dependencies"]) >= {"cloud", "download", "tui", "all"}


def test_check_try_and_verify_import_no_optional_part() -> None:
    code = (
        "import sys; import qedcode.cli, qedcode.tour, qedcode.mcp_server; "
        f"print([m for m in {OPTIONAL!r} if m in sys.modules])"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"
