"""scripts/study_test_weakening.py: the analysis and the statistics (no network in tests)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from tests.conftest import REPO_ROOT


def _load():
    spec = importlib.util.spec_from_file_location(
        "study", REPO_ROOT / "scripts" / "study_test_weakening.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


study = _load()

SKIP = (
    "diff --git a/tests/test_a.py b/tests/test_a.py\n--- a/tests/test_a.py\n+++ b/tests/test_a.py\n"
    "@@ -1,2 +1,3 @@\n+@pytest.mark.skip\n def test_a():\n     assert f() == 1\n"
)
CLEAN = "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"


def test_analyse_flags_weakening_and_counts_test_files() -> None:
    weak = study.analyse(SKIP)
    assert weak["touches_tests"] and weak["kinds"] == ["skip"]
    clean = study.analyse(CLEAN)
    assert not clean["touches_tests"] and clean["weakened"] == {}


def test_wilson_interval() -> None:
    lo, hi = study.wilson(10, 100)
    assert 0.05 < lo < 0.1 < hi < 0.18
    assert study.wilson(0, 0) == (0.0, 0.0)


def test_report_and_sample_read_the_dataset(tmp_path: Path, capsys) -> None:
    rows = [
        {"marker": "claude-code", "repo": "o/r", "sha": "a1", **study.analyse(SKIP)},
        {"marker": "copilot", "repo": "o/s", "sha": "b2", **study.analyse(CLEAN)},
    ]
    (tmp_path / "commits.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    assert study.main(["report", "--data", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "| claude-code | 1 | 1 | 1 |" in out and "| all | 2 | 1 | 1 |" in out
    assert study.main(["sample", "--data", str(tmp_path), "--n", "5"]) == 0
    assert "https://github.com/o/r/commit/a1" in capsys.readouterr().out
