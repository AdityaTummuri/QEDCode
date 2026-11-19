from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qedcode.cli import app
from qedcode.config import ProjectConfig, save_project
from qedcode.engine import api
from qedcode.engine.callbacks import NonInteractiveCallbacks
from qedcode.engine.lock import ProjectBusy, lock_holder, project_lock
from qedcode.models.schemas import Grade, Speed
from qedcode.vcs import worktree
from qedcode.verify.verify import verify_receipt
from tests.conftest import BUGZOO_DIR, make_repo, run_git, tree_state
from tests.zoo_helpers import PY

ZOO = "001_paginate_off_by_one"


def zoo_repo(tmp_path: Path, name: str = ZOO, confirmed: bool = True) -> Path:
    project = tmp_path / "proj"
    shutil.copytree(BUGZOO_DIR / name, project, ignore=shutil.ignore_patterns("__pycache__"))
    for leftover in ("hidden", "edits.txt", "solution.patch"):
        target = project / leftover
        shutil.rmtree(target) if target.is_dir() else target.unlink()
    (project / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n")
    run_git(project, "init", "-q", "-b", "main")
    run_git(project, "add", "-A")
    run_git(project, "commit", "-q", "-m", "buggy")
    if confirmed:
        save_project(project, ProjectConfig(python=PY, confirmed=True))
    return project


def apply_solution(project: Path, name: str = ZOO) -> None:
    patch = (BUGZOO_DIR / name / "solution.patch").read_text(encoding="utf-8")
    proc = run_git(project, "apply", "--whitespace=nowarn", "-", check=False) if False else None
    import subprocess

    done = subprocess.run(
        ["git", "apply", "-"], cwd=project, input=patch, text=True, capture_output=True
    )
    assert done.returncode == 0, done.stderr
    assert proc is None


def copy_hidden_test(project: Path, name: str = ZOO) -> None:
    shutil.copyfile(
        BUGZOO_DIR / name / "hidden" / "test_hidden.py", project / "tests" / "test_hidden.py"
    )


# ------------------------------------------------------------------ the plan's "Done when" checks


def test_solution_applied_by_hand_is_grade_b(tmp_path: Path) -> None:
    project = zoo_repo(tmp_path)
    apply_solution(project)
    outcome = api.check(project)
    assert outcome.status == "graded" and outcome.grade is Grade.B, outcome.message
    assert outcome.summary.startswith("Grade B · ")
    assert "repro test" not in outcome.summary  # nothing proved the bug
    assert outcome.failing_tests == [] and outcome.receipt_path


def test_adding_the_hidden_test_makes_it_grade_a(tmp_path: Path) -> None:
    project = zoo_repo(tmp_path)
    apply_solution(project)
    copy_hidden_test(project)
    outcome = api.check(project)
    assert outcome.grade is Grade.A, outcome.message
    assert "repro test failed before, passes now" in outcome.summary
    receipt_file = Path(outcome.receipt_path)
    assert receipt_file == project / ".qed" / "receipts" / f"{outcome.task_id}.json"
    assert outcome.candidate and outcome.candidate.role == "user"
    assert [c.stage for c in outcome.candidate.checks] == [
        "parse",
        "scope",
        "risk",
        "lint",
        "fbpa",
        "targeted",
        "full",
    ]  # no apply stage: nothing was generated
    verified = verify_receipt(receipt_file, project)
    assert verified.verified, verified.mismatches


def test_the_hidden_test_without_the_fix_is_rejected_with_the_failing_tests(tmp_path: Path) -> None:
    project = zoo_repo(tmp_path)
    copy_hidden_test(project)
    outcome = api.check(project)
    assert outcome.status == "rejected" and outcome.grade is None
    assert "Rejected at fbpa" in outcome.message
    assert any("test_full_pages_have_the_page_size" in k for k in outcome.failing_tests)
    assert outcome.receipt_path is None


# ------------------------------------------------------------------ other situations


def test_check_never_touches_the_users_files_and_leaves_no_worktrees(tmp_path: Path) -> None:
    project = zoo_repo(tmp_path)
    apply_solution(project)
    copy_hidden_test(project)
    from qedcode.vcs.snapshot import ensure_excluded

    ensure_excluded(project)  # QEDCode hides its own .qed/ folder from git status, once
    before = tree_state(project)
    status = run_git(project, "status", "--porcelain", "-z")
    index = (project / ".git" / "index").read_bytes()
    api.check(project)
    assert tree_state(project) == before
    assert run_git(project, "status", "--porcelain", "-z") == status
    assert (project / ".git" / "index").read_bytes() == index
    assert worktree.list_worktrees(project) == []
    assert run_git(project, "log", "--oneline").count("\n") == 1  # no commits were made on main


def test_no_changes_means_nothing_to_check(tmp_path: Path) -> None:
    project = zoo_repo(tmp_path)
    outcome = api.check(project)
    assert outcome.status == "no_changes" and "nothing to check" in outcome.message
    assert "--base" in outcome.message


def test_a_change_that_breaks_a_visible_test_is_rejected(tmp_path: Path) -> None:
    project = zoo_repo(tmp_path)
    pages = project / "src" / "shop" / "pages.py"
    pages.write_text(pages.read_text().replace("(total + size - 1) // size", "total // size"))
    outcome = api.check(project)
    assert outcome.status == "rejected" and outcome.grade is None
    assert outcome.failing_tests == ["tests.test_pages::test_page_count_rounds_up"]
    assert "Rejected at targeted" in outcome.message


def test_a_syntax_error_is_rejected_at_parse(tmp_path: Path) -> None:
    project = zoo_repo(tmp_path)
    (project / "src" / "shop" / "pages.py").write_text("def broken(:\n")
    outcome = api.check(project)
    assert outcome.status == "rejected" and "Rejected at parse" in outcome.message


def test_risky_code_in_your_own_change_is_only_a_warning(tmp_path: Path) -> None:
    project = zoo_repo(tmp_path)
    apply_solution(project)
    pages = project / "src" / "shop" / "pages.py"
    pages.write_text(pages.read_text() + "\nimport subprocess\n")
    outcome = api.check(project)
    assert outcome.grade is Grade.B  # not rejected
    risk = next(c for c in outcome.candidate.checks if c.stage == "risk")
    assert risk.passed and not risk.needs_approval and risk.summary.startswith("Warning:")
    receipt = json.loads(Path(outcome.receipt_path).read_text())
    assert any("risky code" in g for g in receipt["unverified"])


def test_check_against_an_older_commit_includes_committed_changes(tmp_path: Path) -> None:
    project = zoo_repo(tmp_path)
    apply_solution(project)
    run_git(project, "add", "-A")
    run_git(project, "commit", "-q", "-m", "the fix")
    assert api.check(project).status == "no_changes"  # HEAD equals the working tree
    outcome = api.check(project, base="HEAD~1")
    assert outcome.status == "graded" and outcome.grade is Grade.B
    assert outcome.base == "HEAD~1"
    assert verify_receipt(outcome.receipt_path, project).verified


def test_fast_speed_skips_the_full_suite_and_caps_at_b(tmp_path: Path) -> None:
    project = zoo_repo(tmp_path)
    apply_solution(project)
    copy_hidden_test(project)
    outcome = api.check(project, speed=Speed.FAST)
    assert outcome.grade is Grade.B
    stages = [c.stage for c in outcome.candidate.checks]
    assert "full" not in stages and "fbpa" in stages
    receipt = json.loads(Path(outcome.receipt_path).read_text())
    assert "Full suite not run (fast speed)." in receipt["unverified"]


def test_events_are_reported_through_the_callbacks(tmp_path: Path) -> None:
    project = zoo_repo(tmp_path)
    apply_solution(project)
    cb = NonInteractiveCallbacks()
    api.check(project, cb=cb)
    kinds = {e.kind for e in cb.events}
    assert {"stage", "check", "candidate", "result"} <= kinds
    assert cb.events[0].message.startswith("Taking a snapshot")
    assert cb.events[-1].kind == "result" and cb.events[-1].data["grade"] == "B"


def test_cancelling_returns_a_cancelled_outcome(tmp_path: Path) -> None:
    project = zoo_repo(tmp_path)
    apply_solution(project)
    state = {"n": 0}

    def cancel() -> bool:
        state["n"] += 1
        return state["n"] > 3

    outcome = api.check(project, cb=NonInteractiveCallbacks(cancelled=cancel))
    assert outcome.status == "cancelled"
    assert worktree.list_worktrees(project) == []


def test_not_a_git_repository_and_no_commits_and_a_bad_base(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert "not a git repository" in api.check(plain).message
    empty = make_repo(tmp_path / "empty", {"a.py": "A = 1\n"}, commit=False)
    assert "no commits yet" in api.check(empty).message
    project = zoo_repo(tmp_path)
    outcome = api.check(project, base="no-such-branch")
    assert outcome.status == "error" and "--base" in outcome.message


def test_an_unconfirmed_project_uses_the_detected_settings_with_a_note(
    tmp_path: Path, monkeypatch
) -> None:
    project = zoo_repo(tmp_path, confirmed=False)
    apply_solution(project)
    from qedcode.project import detect as detect_module

    real = detect_module.detect_project

    def detect_with_this_python(root, **kw):
        found = real(root, which=lambda name: PY, **kw)
        return found

    monkeypatch.setattr("qedcode.engine.check.detect_project", detect_with_this_python)
    outcome = api.check(project)
    assert outcome.grade is Grade.B
    assert any("Run `qed` once in this folder to confirm" in n for n in outcome.notes)
    assert not (project / ".qed" / "project.toml").exists()  # nothing is saved without a yes


def test_a_second_job_in_the_same_project_is_refused(tmp_path: Path) -> None:
    project = zoo_repo(tmp_path)
    apply_solution(project)
    with project_lock(project, "t-other"):
        assert lock_holder(project)["task"] == "t-other"
        outcome = api.check(project)
        assert outcome.status == "busy" and "already running" in outcome.message
    assert lock_holder(project) is None
    assert api.check(project).status == "graded"  # the lock was released


# ------------------------------------------------------------------ the project lock


def test_lock_is_released_after_an_error_and_stale_locks_are_taken_over(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        with project_lock(tmp_path):
            raise RuntimeError("boom")
    assert lock_holder(tmp_path) is None
    stale = tmp_path / ".qed" / "lock"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text(json.dumps({"pid": 2**22 - 7, "since": 0, "task": "dead"}))
    with project_lock(tmp_path, "new"):  # the old owner is gone: no ProjectBusy
        assert lock_holder(tmp_path)["task"] == "new"
    with project_lock(tmp_path):
        with pytest.raises(ProjectBusy):
            with project_lock(tmp_path):
                pass


# ------------------------------------------------------------------ the CLI


def test_cli_check_prints_the_grade_summary_and_receipt(tmp_path: Path, monkeypatch) -> None:
    project = zoo_repo(tmp_path)
    apply_solution(project)
    copy_hidden_test(project)
    monkeypatch.chdir(project)
    result = CliRunner().invoke(app, ["check"])
    assert result.exit_code == 0, result.output
    assert "Grade A" in result.output
    assert "repro test failed before, passes now" in result.output
    assert "Receipt:" in result.output and "qed verify" in result.output


def test_cli_check_json(tmp_path: Path, monkeypatch) -> None:
    project = zoo_repo(tmp_path)
    apply_solution(project)
    monkeypatch.chdir(project)
    result = CliRunner().invoke(app, ["check", "--json", "--speed", "fast"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["status"] == "graded" and data["grade"] == "B"
    assert data["summary"].startswith("Grade B") and data["receipt_path"].endswith(".json")
    assert data["failing_tests"] == [] and "candidate" not in data


def test_cli_check_exit_codes_and_messages(tmp_path: Path, monkeypatch) -> None:
    project = zoo_repo(tmp_path)
    monkeypatch.chdir(project)
    nothing = CliRunner().invoke(app, ["check"])
    assert nothing.exit_code == 2 and "nothing to check" in nothing.output
    bad_speed = CliRunner().invoke(app, ["check", "--speed", "warp"])
    assert bad_speed.exit_code == 2 and "fast, normal or sure" in bad_speed.output
    (project / "src" / "shop" / "pages.py").write_text("def broken(:\n")
    rejected = CliRunner().invoke(app, ["check"])
    assert rejected.exit_code == 1 and "Rejected" in rejected.output
    assert "syntax error" in rejected.output.lower() or "Syntax error" in rejected.output


def test_cli_check_json_output_has_no_progress_lines_on_stdout(tmp_path: Path, monkeypatch) -> None:
    project = zoo_repo(tmp_path)
    apply_solution(project)
    monkeypatch.chdir(project)
    result = CliRunner().invoke(app, ["check", "--json"])
    json.loads(result.stdout)  # the whole stdout is one JSON document
