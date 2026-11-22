"""scripts/screenshots.py keeps working: CI regenerates every README image with it."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

from tests.conftest import REPO_ROOT


def _load():
    spec = importlib.util.spec_from_file_location(
        "screenshots", REPO_ROOT / "scripts" / "screenshots.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_the_screenshots_show_a_graded_result(tmp_path: Path) -> None:
    shots = _load()
    saved = asyncio.run(shots.capture(tmp_path))
    assert [p.name for p in saved] == list(shots.SHOTS)
    card = (tmp_path / "result-card.svg").read_text(encoding="utf-8")
    assert card.startswith("<svg") and "Grade" in card and "scripted" in card
    assert "Proof" in (tmp_path / "receipt-view.svg").read_text(encoding="utf-8")


def test_the_readme_uses_a_generated_screenshot() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/assets/result-card.svg" in readme
    assert (REPO_ROOT / "docs" / "assets" / "result-card.svg").is_file()
