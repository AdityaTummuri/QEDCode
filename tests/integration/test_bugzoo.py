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
