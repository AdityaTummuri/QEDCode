"""scripts/readme_stats.py: the README's numbers are measured, never typed by hand."""

from __future__ import annotations

import importlib.util

import pytest

from tests.conftest import REPO_ROOT


def _load():
    spec = importlib.util.spec_from_file_location(
        "readme_stats", REPO_ROOT / "scripts" / "readme_stats.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


stats = _load()


def test_platforms_come_from_the_ci_matrix() -> None:
    systems, pythons = stats.platforms()
    assert systems == ["Linux", "macOS", "Windows"]
    assert pythons == ["3.11", "3.12"]


def test_source_lines_are_counted_without_the_demo_projects() -> None:
    assert stats.source_lines() > 10_000


def test_the_block_replaces_only_what_is_between_the_markers() -> None:
    block = stats.render(14_000, 1_300, ["Linux", "macOS", "Windows"], ["3.11", "3.12"], "0.2.0")
    assert "14,000 lines of source · 1,300 automated tests" in block
    assert "Linux, macOS and Windows" in block and "v0.2.0" in block
    text = f"before\n{stats.START}\nold\n{stats.END}\nafter\n"
    new = stats.replace_block(text, block)
    assert new.startswith("before\n") and new.endswith("\nafter\n") and "old" not in new
    with pytest.raises(ValueError, match="markers"):
        stats.replace_block("no markers here", block)


def test_the_readme_has_the_markers() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert stats.START in readme and stats.END in readme
    assert "1,118" not in readme  # the old hand-typed badge
