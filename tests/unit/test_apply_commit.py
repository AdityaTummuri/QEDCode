"""Proof-carrying commits (AGENTS.md 15.10): `qed apply --commit` puts the receipt in git notes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qedcode import tour
from qedcode.cli import app
from qedcode.engine import api
from qedcode.engine.callbacks import NonInteractiveCallbacks
from qedcode.vcs.git import git

runner = CliRunner()


@pytest.fixture
def solved(tmp_path: Path) -> Path:
    result = tour.run_tour(
        "001_paginate_off_by_one",
        tmp_path,
        client=None,
        cb=NonInteractiveCallbacks(),
        scripted=True,
    )
    repo = result.project
    git(repo, "config", "user.name", "Ada Lovelace")
    git(repo, "config", "user.email", "ada@example.com")
    return repo


def test_apply_commit_records_grade_receipt_and_note(solved: Path, monkeypatch) -> None:
    (solved / "notes.txt").write_text("my own work in progress\n")
    git(solved, "add", "notes.txt")  # staged work of the user's that must not be committed
    monkeypatch.chdir(solved)
    result = runner.invoke(app, ["apply", "--commit"])
    assert result.exit_code == 0, result.output
    flat = " ".join(result.output.split())
    assert "committed as" in flat and "Git hooks did not run" in flat
    log = git(solved, "log", "-1", "--format=%an%n%B").stdout
    assert not log.startswith("QEDCode\n")  # the user's identity, not QEDCode's
    assert "QED-Grade: A" in log and "QED-Receipt: sha256:" in log
    assert "Grade A · repro test failed before, passes now" in log
    shown = git(solved, "log", "-1", "--notes=qed", "--format=%N").stdout
    assert json.loads(shown)["grade"] == "A"
    committed = git(solved, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert "src/shop/pages.py" in committed and "notes.txt" not in committed
    assert "notes.txt" in git(solved, "diff", "--cached", "--name-only").stdout  # still staged


def test_qed_receipt_reads_a_commit_and_undo_points_to_revert(solved: Path, monkeypatch) -> None:
    monkeypatch.chdir(solved)
    assert api.apply_result(solved, commit=True).status == "applied"
    shown = runner.invoke(app, ["receipt", "--commit", "HEAD"])
    assert shown.exit_code == 0, shown.output
    assert shown.stdout.startswith("Grade A")
    before = runner.invoke(app, ["receipt", "--commit", "HEAD~1"])
    assert before.exit_code == 2 and "carries no QEDCode receipt" in before.output
    undo = runner.invoke(app, ["undo"])
    assert undo.exit_code == 1 and "git revert" in undo.output


def test_a_failed_commit_says_what_to_do(solved: Path, monkeypatch) -> None:
    git(solved, "config", "--unset", "user.email")
    git(solved, "config", "--unset", "user.name")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "")
    monkeypatch.setenv("EMAIL", "")
    outcome = api.apply_result(solved, commit=True)
    if outcome.status == "error":  # no identity anywhere: the files are applied, not committed
        assert "could not be committed" in outcome.message and "qed undo" in outcome.message
    else:  # the machine has a system-wide identity
        assert "committed as" in outcome.message
