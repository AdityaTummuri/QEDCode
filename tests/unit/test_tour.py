"""`qed try`: the bundled demo reaches a verified grade A without a GPU (AGENTS.md 15.7)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from qedcode import tour
from qedcode.cli import app
from qedcode.config import GlobalConfig, save_global
from qedcode.engine.callbacks import NonInteractiveCallbacks
from qedcode.models.schemas import Grade
from tests.conftest import BUGZOO_DIR

runner = CliRunner()


def _files(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


@pytest.mark.parametrize("name", tour.list_projects())
def test_bundled_projects_are_exact_copies_of_the_bug_zoo(name: str) -> None:
    bundled = _files(tour.TOUR_DIR / name)
    assert set(bundled) - {"script.yaml"}, name
    zoo = BUGZOO_DIR / name
    for rel, data in bundled.items():
        if rel != "script.yaml":
            assert (zoo / rel).read_bytes() == data, rel
    for rel in ("task.txt", "pyproject.toml"):
        assert rel in bundled
    # nothing that would give the answer away ships with the demo
    assert not any(r.startswith("hidden/") or r in ("solution.patch", "edits.txt") for r in bundled)


def test_three_projects_ship_and_names_resolve() -> None:
    assert tour.list_projects() == [
        "001_paginate_off_by_one",
        "004_empty_input_crash",
        "007_sort_order",
    ]
    assert tour.find_project(None) == "001_paginate_off_by_one"
    assert tour.find_project("7") == tour.find_project("007") == "007_sort_order"
    assert tour.find_project("empty") == "004_empty_input_crash"
    with pytest.raises(tour.TourError, match="qed try --list"):
        tour.find_project("nope")


@pytest.mark.parametrize("name", tour.list_projects())
def test_the_scripted_tour_rejects_the_cheat_and_proves_the_fix(tmp_path: Path, name: str) -> None:
    result = tour.run_tour(name, tmp_path, client=None, cb=NonInteractiveCallbacks(), scripted=True)
    assert result.scripted
    assert result.solve.status == "done"
    first, *_ = result.solve.candidates
    assert first.grade is None and first.failed_stage == "scope"  # the conftest.py cheat
    assert result.solve.best is not None and result.solve.best.grade is Grade.A
    assert result.verify is not None and result.verify.verified, result.verify.mismatches


def test_try_prints_the_proof_and_leaves_the_current_folder_alone(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["try", "--solve"])
    assert result.exit_code == 0, result.stdout
    out = result.stdout
    assert tour.SCRIPTED_NOTE in out
    assert "rejected at scope" in out and "Grade A" in out and "VERIFIED" in out
    for command, _why in tour.next_steps():
        assert command in out
    assert list(tmp_path.iterdir()) == []


def test_the_demo_works_when_git_settings_come_from_the_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "safe.directory")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "*")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["try", "--scripted"])
    assert result.exit_code == 0, result.stdout
    assert "VERIFIED" in result.stdout


def test_try_keep_leaves_the_demo_project(tmp_path: Path, monkeypatch) -> None:
    kept: list[Path] = []
    real_make = tour.make_dest

    def make() -> Path:
        kept.append(real_make())
        return kept[-1]

    monkeypatch.setattr(tour, "make_dest", make)
    result = runner.invoke(app, ["try", "004", "--keep"])
    assert result.exit_code == 0, result.stdout
    assert "kept in" in result.stdout
    assert (kept[0] / "004_empty_input_crash" / ".qed" / "receipts").is_dir()


def test_try_list_names_every_project() -> None:
    result = runner.invoke(app, ["try", "--list"])
    assert result.exit_code == 0
    for name in tour.list_projects():
        assert name in result.stdout


def test_a_set_up_model_that_is_not_ready_falls_back_to_the_script(monkeypatch) -> None:
    save_global(GlobalConfig())  # `qed setup` has run

    def broken(config, role):
        from qedcode.runtime.client import ModelError

        raise ModelError("the model file is missing")

    monkeypatch.setattr("qedcode.cli.make_client", broken)
    result = runner.invoke(app, ["try", "--solve"])
    assert result.exit_code == 0, result.stdout
    assert "the model file is missing" in result.stdout and tour.SCRIPTED_NOTE in result.stdout
    strict = runner.invoke(app, ["try", "--real"])
    assert strict.exit_code == 2


def test_an_unknown_project_is_a_clear_error() -> None:
    result = runner.invoke(app, ["try", "zzz"])
    assert result.exit_code == 2
    assert "qed try --list" in result.output


def test_the_default_tour_shows_caught_unproven_and_proven(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["try"], env={"COLUMNS": "120"})
    assert result.exit_code == 0, result.stdout
    lines = [x for x in result.stdout.splitlines() if x.strip()]
    changes = [x for x in lines if x.strip().startswith("change ")]
    assert [x.split()[2:4] for x in changes] == [
        ["C", "Caught"],
        ["B", "Unproven"],
        ["A", "Proven"],
    ]
    assert "VERIFIED" in result.stdout and "NOT VERIFIED" not in result.stdout
    assert len(lines) <= 12
    assert list(tmp_path.iterdir()) == []


def test_the_default_tour_can_print_every_check(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["try", "--verbose"])
    assert result.exit_code == 0, result.stdout
    assert "fbpa" in result.stdout and "Caught:" in result.stdout
