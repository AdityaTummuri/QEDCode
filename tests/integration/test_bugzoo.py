from __future__ import annotations

import pytest

from tests.conftest import BUGZOO_DIR, zoo_project_names


def test_the_zoo_has_ten_projects() -> None:
    assert len(zoo_project_names()) == 10


@pytest.mark.parametrize("name", zoo_project_names())
def test_each_project_is_valid(check_zoo, name: str) -> None:
    result = check_zoo.check_project(BUGZOO_DIR / name)
    assert result.valid, result.problems


@pytest.mark.parametrize("name", zoo_project_names())
def test_each_project_is_small_and_complete(name: str) -> None:
    project = BUGZOO_DIR / name
    task = (project / "task.txt").read_text(encoding="utf-8").strip()
    assert 10 < len(task) < 300
    code_lines = sum(
        len(p.read_text(encoding="utf-8").splitlines()) for p in (project / "src").rglob("*.py")
    )
    assert code_lines < 60
    assert (project / "edits.txt").read_text(encoding="utf-8").lstrip().startswith("PLAN:")


def test_run_zoo_reports_grades_and_checks_the_hidden_test(tmp_path, monkeypatch) -> None:
    """bugzoo/run_zoo.py with scripted models: every project is solved and its fix is really right."""
    import importlib.util
    import sys

    from qedcode.runtime.fake import FakeModel
    from tests.zoo_helpers import script_for

    spec = importlib.util.spec_from_file_location("run_zoo", BUGZOO_DIR / "run_zoo.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_zoo"] = module
    spec.loader.exec_module(module)
    runs = [
        module.run_project(BUGZOO_DIR / name, "normal", lambda n: FakeModel(script_for(n)))
        for name in ("001_paginate_off_by_one", "005_integer_average", "008_slugify_feature")
    ]
    assert [r.grade for r in runs] == ["A", "A", "A"]
    assert all(r.status == "done" and r.hidden_passes is True for r in runs)
    summary = module.summarize(runs)
    assert (
        summary["grade_A"] == 3
        and summary["no_fix"] == 0
        and summary["wrong_but_graded_A_or_B"] == 0
    )
