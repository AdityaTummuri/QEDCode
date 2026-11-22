"""scripts/changelog_section.py: release notes come from the CHANGELOG, not from memory."""

from __future__ import annotations

import importlib.util

import pytest

from qedcode import __version__
from tests.conftest import REPO_ROOT


def _load():
    spec = importlib.util.spec_from_file_location(
        "changelog_section", REPO_ROOT / "scripts" / "changelog_section.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


notes = _load()
TEXT = "# Changelog\n\n## [Unreleased]\n\n## [0.2.0] - 2026-11-30\n\n- new\n\n## [0.1.0]\n\n- old\n"


def test_one_section_is_cut_out() -> None:
    assert notes.section(TEXT, "v0.2.0") == "- new\n"
    assert notes.section(TEXT, "0.1.0") == "- old\n"


def test_a_missing_or_empty_section_is_an_error() -> None:
    with pytest.raises(ValueError, match="0.3.0"):
        notes.section(TEXT, "0.3.0")
    with pytest.raises(ValueError):
        notes.section("## [0.4.0]\n\n## [0.3.0]\n- x\n", "0.4.0")


def test_the_current_version_has_notes() -> None:
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert notes.section(changelog, __version__).strip()
