from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qedcode.cli import app
from qedcode.engine import api
from qedcode.models.schemas import Baseline, Candidate, CheckResult, Grade, Speed
from qedcode.verify.cascade import evaluate_candidate
from qedcode.verify.receipt import (
    NETWORK_NOTE,
    build_receipt,
    load_receipt,
    receipt_path,
    save_receipt,
    summary_line,
    templatize_command,
)
from qedcode.verify.verify import verify_receipt
from tests.zoo_helpers import PY, REPRO_PATH, ZooCase, zoo_case


def graded_a(tmp_path: Path, **kw):
    case = zoo_case(tmp_path, **kw)
    cand = evaluate_candidate(case.ctx, case.candidate(), run_full=True)
    assert cand.grade is Grade.A
    return case, cand


def make_receipt(case: ZooCase, cand: Candidate, **kw):
    return build_receipt(
        root=case.project,
        task=case.ctx.task,
        cand=cand,
        baseline=case.ctx.baseline,
        project=case.ctx.project,
        base_commit=case.ctx.base_commit,
        base_ref=f"refs/qed/snapshots/{case.ctx.task.id}",
        **kw,
    )


@pytest.fixture(scope="module")
def proven(tmp_path_factory):
    """One real grade-A candidate and its saved receipt, shared by the read-only verify tests."""
    tmp = tmp_path_factory.mktemp("proven")
    case, cand = graded_a(tmp)
    receipt = make_receipt(case, cand, model_info={"backend": "fake", "model": "fake:scripted"})
    path = save_receipt(case.project, receipt)
    return case, cand, receipt, path


# ------------------------------------------------------------------ receipts


def test_build_receipt_fields(proven) -> None:
    case, cand, receipt, _ = proven
    assert receipt.schema_version == 1 and receipt.grade is Grade.A
    assert receipt.project_name == "001_paginate_off_by_one"
    assert receipt.base_commit == case.ctx.base_commit
    assert receipt.base_ref == f"refs/qed/snapshots/{case.ctx.task.id}"
    assert receipt.patch == cand.diff
    assert receipt.patch_sha256 == hashlib.sha256(cand.diff.encode()).hexdigest()
    assert receipt.tests_added == [REPRO_PATH]
    assert len(receipt.fbpa_tests) == 2 and all(
        k.startswith("tests.test_qed_repro::") for k in receipt.fbpa_tests
    )
    assert receipt.known_failures == [] and receipt.protected_changes == []
    assert [c.stage for c in receipt.checks] == [c.stage for c in cand.checks]
    assert receipt.created_at.endswith("Z") and receipt.qed_version == "0.1.0"
    assert receipt.model_info == {"backend": "fake", "model": "fake:scripted"}


def test_commands_use_placeholders_and_start_with_python(proven) -> None:
    _, _, receipt, _ = proven
    assert len(receipt.commands) == 3  # fbpa, targeted, full
    for command in receipt.commands:
        assert command[:3] == ["{python}", "-m", "pytest"]
        assert command[command.index("--junitxml") + 1] == "{report}"
        assert PY not in command
    assert receipt.commands[0][-1] == REPRO_PATH  # the fbpa run selects the repro file


def test_templatize_command() -> None:
    argv = [
        "/venv/bin/python",
        "-m",
        "pytest",
        "-q",
        "--junitxml",
        "/tmp/x/report.xml",
        "tests/a.py",
    ]
    assert templatize_command(argv, "/venv/bin/python") == [
        "{python}",
        "-m",
        "pytest",
        "-q",
        "--junitxml",
        "{report}",
        "tests/a.py",
    ]


def test_summary_line_matches_the_brief(proven) -> None:
    _, _, receipt, _ = proven
    line = summary_line(receipt)
    assert line.startswith("Grade A · repro test failed before, passes now · ")
    assert line.endswith(" tests pass · lint clean · no protected files changed")
    assert "/" in line.split(" · ")[2]


def test_unverified_list_is_plain_english(proven) -> None:
    _, _, receipt, _ = proven
    assert NETWORK_NOTE in receipt.unverified
    assert not any("Full suite not run" in g for g in receipt.unverified)  # the full suite ran
    assert not any("fail before" in g for g in receipt.unverified)  # there was a repro proof


def test_unverified_gaps_for_a_weaker_result(tmp_path: Path) -> None:
    case = zoo_case(tmp_path, repro=False, speed=Speed.FAST, full_baseline=False)
    cand = evaluate_candidate(case.ctx, case.candidate(), run_full=False)
    receipt = make_receipt(case, cand, model_info={"backend": "cloud", "model": "x/y"})
    gaps = receipt.unverified
    assert "Full suite not run (fast speed)." in gaps
    assert "No test was shown to fail before the change and pass after it." in gaps
    assert any("sent to a cloud model (x/y)" in g for g in gaps)
    assert summary_line(receipt).startswith("Grade B · ") and "repro test" not in summary_line(
        receipt
    )
    helper = make_receipt(case, cand, model_info={"helper_used": True, "helper": "big-model"})
    assert any("helper model (big-model)" in g for g in helper.unverified)


def test_known_failures_and_risk_approvals_are_listed(tmp_path: Path) -> None:
    case = zoo_case(tmp_path, repro=False, confirm=lambda p: True)
    case.ctx.baseline = Baseline(commit="x", results={"t.a::b": "failed", "t.a::c": "passed"})
    risky = (
        "EDIT search_replace path=src/shop/pages.py\n<<<<<<< SEARCH\n    end = start + size - 1\n"
        "=======\n    import subprocess\n    end = start + size\n>>>>>>> REPLACE\n"
    )
    cand = evaluate_candidate(case.ctx, case.candidate(risky), run_full=True)
    receipt = make_receipt(case, cand)
    assert receipt.known_failures == ["t.a::b"]
    assert any("1 test(s) were already failing" in g and "t.a::b" in g for g in receipt.unverified)
    assert "You approved a risky pattern: subprocess in src/shop/pages.py." in receipt.unverified


def test_a_rejected_candidate_gets_no_receipt(tmp_path: Path) -> None:
    case = zoo_case(tmp_path)
    cand = evaluate_candidate(case.ctx, case.candidate("no edits here"))
    with pytest.raises(ValueError):
        make_receipt(case, cand)


def test_save_and_load_round_trip(proven) -> None:
    case, _, receipt, path = proven
    assert path == receipt_path(case.project, case.ctx.task.id)
    assert path == case.project / ".qed" / "receipts" / f"{case.ctx.task.id}.json"
    assert load_receipt(path) == receipt


def test_a_receipt_never_contains_prompts_or_secrets(proven) -> None:
    _, _, _, path = proven
    text = path.read_text(encoding="utf-8")
    for needle in ("api_key", "sk-", "Bearer"):
        assert needle not in text


def test_load_receipt_errors_are_plain(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as err:
        load_receipt(tmp_path / "missing.json")
    assert "Cannot read the receipt" in str(err.value)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(ValueError) as err2:
        load_receipt(bad)
    assert "not a valid QEDCode receipt" in str(err2.value)


# ------------------------------------------------------------------ verify


def test_an_untouched_receipt_verifies(proven) -> None:
    case, _, receipt, path = proven
    result = verify_receipt(path, case.project)
    assert result.verified, result.mismatches
    assert result.mismatches == [] and result.receipt == receipt


def tampered(path: Path, tmp: Path, edit) -> Path:
    data = json.loads(path.read_text(encoding="utf-8"))
    edit(data)
    out = tmp / "tampered.json"
    out.write_text(json.dumps(data), encoding="utf-8")
    return out


def test_editing_the_patch_text_is_detected(proven, tmp_path: Path) -> None:
    case, _, _, path = proven
    bad = tampered(path, tmp_path, lambda d: d.update(patch=d["patch"].replace("size", "SIZE", 1)))
    result = verify_receipt(bad, case.project)
    assert not result.verified
    assert "checksum does not match" in result.mismatches[0]


def test_a_patch_edited_together_with_its_checksum_still_fails_the_proof(
    proven, tmp_path: Path
) -> None:
    case, _, _, path = proven

    def drop_the_fix(data: dict) -> None:
        # keep only the new test file in the patch: the fix is gone, the checksum is "right"
        parts = data["patch"].split("diff --git ")
        kept = [p for p in parts if p and "src/shop/pages.py" not in p.split("\n", 1)[0]]
        data["patch"] = "".join("diff --git " + p for p in kept)
        data["patch_sha256"] = hashlib.sha256(data["patch"].encode()).hexdigest()

    result = verify_receipt(tampered(path, tmp_path, drop_the_fix), case.project)
    assert not result.verified
    assert any("should pass with the change but it does not" in m for m in result.mismatches)


def test_a_flipped_check_result_is_detected(proven, tmp_path: Path) -> None:
    case, _, _, path = proven

    def flip(data: dict) -> None:
        next(c for c in data["checks"] if c["stage"] == "targeted")["passed"] = False

    result = verify_receipt(tampered(path, tmp_path, flip), case.project)
    assert not result.verified
    assert any(
        "targeted check was recorded as failed but now passes" in m for m in result.mismatches
    )


def test_a_fail_before_test_that_does_not_fail_before_is_detected(proven, tmp_path: Path) -> None:
    case, _, _, path = proven

    def claim_a_passing_test(data: dict) -> None:
        # "test_last_partial_page..." passes on the buggy code too, so it is no proof
        data["fbpa_tests"].append(
            "tests.test_qed_repro::test_last_partial_page_keeps_its_last_item"
        )

    result = verify_receipt(tampered(path, tmp_path, claim_a_passing_test), case.project)
    assert not result.verified
    assert any("supposed to fail before the change" in m for m in result.mismatches)


def test_a_receipt_from_another_repository_is_explained(proven, tmp_path: Path) -> None:
    case, _, _, path = proven
    other = tmp_path / "other"
    other.mkdir()
    from tests.conftest import make_repo

    make_repo(other, {"a.py": "A = 1\n"})
    result = verify_receipt(path, other)
    assert not result.verified
    assert (
        "not in this repository" in result.mismatches[0]
        and "inside the repository where the receipt was made" in result.mismatches[0]
    )


def test_an_invalid_receipt_file_is_reported(tmp_path: Path) -> None:
    bad = tmp_path / "r.json"
    bad.write_text("[]")
    result = verify_receipt(bad, tmp_path)
    assert not result.verified and "not a valid QEDCode receipt" in result.mismatches[0]


def test_verify_leaves_no_worktrees_behind(proven) -> None:
    from qedcode.vcs import worktree

    case, _, _, path = proven
    verify_receipt(path, case.project)
    assert worktree.list_worktrees(case.project) == []


# ------------------------------------------------------------------ the CLI


def test_qed_verify_prints_verified(proven, monkeypatch) -> None:
    case, _, _, path = proven
    monkeypatch.chdir(case.project)
    result = CliRunner().invoke(app, ["verify", str(path)])
    assert result.exit_code == 0, result.output
    assert "VERIFIED" in result.output and "Grade A" in result.output


def test_qed_verify_reports_mismatches_with_exit_code_1(
    proven, tmp_path: Path, monkeypatch
) -> None:
    case, _, _, path = proven
    bad = tampered(path, tmp_path, lambda d: d.update(patch="diff --git a/x b/x\n"))
    monkeypatch.chdir(case.project)
    result = CliRunner().invoke(app, ["verify", str(bad)])
    assert result.exit_code == 1
    assert "NOT VERIFIED" in result.output and "checksum" in result.output


def test_engine_api_verify_resolves_the_repo_root(proven, monkeypatch) -> None:
    case, _, _, path = proven
    monkeypatch.chdir(case.project / "src")
    assert api.verify(path).verified
    assert api.resolve_root() == case.project.resolve() or api.resolve_root().samefile(case.project)


def test_check_result_import_is_used() -> None:
    assert CheckResult(stage="apply", passed=True).stage == "apply"


# ------------------------------------------------------------------ untrusted receipts


def _with_command(data: dict, command: list[str]) -> None:
    data["commands"][0] = command  # the first check that ran a command


def test_a_receipt_cannot_run_a_command_of_its_own(proven, tmp_path: Path) -> None:
    case, _, _, path = proven
    marker = tmp_path / "ran.txt"
    evil = [PY, "-c", f"open({str(marker)!r}, 'w').write('x')", "-q"]
    bad = tampered(path, tmp_path, lambda d: _with_command(d, evil))
    result = verify_receipt(bad, case.project)
    assert not result.verified
    assert "not safe to run here" in result.mismatches[0]
    assert "test command" in result.mismatches[0]
    assert not marker.exists()


@pytest.mark.parametrize(
    "item",
    ["--basetemp=/tmp/x", "-p", "../outside/test_x.py", "/abs/test_x.py", ".git/hooks/x.py"],
)
def test_a_receipt_cannot_smuggle_options_into_the_test_selection(
    proven, tmp_path: Path, item: str
) -> None:
    case, _, receipt, path = proven
    original = receipt.commands[0]
    assert "--junitxml" in original

    def add(data: dict) -> None:
        _with_command(data, [*original, item])

    result = verify_receipt(tampered(path, tmp_path, add), case.project)
    assert not result.verified
    assert "not safe to run here" in result.mismatches[0]


def test_the_projects_own_test_command_is_allowed(proven) -> None:
    from qedcode.verify.verify import unsafe_command

    case, _, receipt, _ = proven
    for command in receipt.commands:
        assert unsafe_command(command, case.ctx.project) is None


# ------------------------------------------------------------------ which Python re-runs the tests


def _project_with_venv(tmp_path: Path) -> Path:
    import os
    import sys

    root = tmp_path / "proj"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_x.py").write_text("def test_x():\n    assert True\n")
    (root / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0"\n')
    bindir = root / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    bindir.mkdir(parents=True)
    target = bindir / ("python.exe" if os.name == "nt" else "python")
    target.symlink_to(sys.executable)
    return root


@pytest.mark.skipif(__import__("os").name == "nt", reason="symlinks need extra rights on Windows")
def test_an_unconfirmed_project_reruns_with_its_detected_python(tmp_path: Path) -> None:
    """Found by the agent skill's CI: with QEDCode installed as a `uv tool` (no pytest in its own
    environment), verify used QEDCode's interpreter and called a sound receipt NOT VERIFIED."""
    from qedcode.verify.verify import _project_config

    root = _project_with_venv(tmp_path)
    notes: list[str] = []
    cfg = _project_config(root, notes)
    assert Path(cfg.python).parent.parent == root / ".venv"
    assert any("detected Python" in n for n in notes)


def test_a_confirmed_project_config_wins_over_detection(tmp_path: Path) -> None:
    from qedcode.config import ProjectConfig, save_project
    from qedcode.verify.verify import _project_config

    root = tmp_path / "proj"
    root.mkdir()
    save_project(root, ProjectConfig(python="/opt/py/bin/python", confirmed=True))
    notes: list[str] = []
    assert _project_config(root, notes).python == "/opt/py/bin/python" and notes == []


def test_summary_says_why_a_no_code_change_is_grade_c(tmp_path: Path) -> None:
    """Grade C next to "67/67 tests pass" needs its reason on the same line."""
    case = zoo_case(tmp_path, repro=False)
    gitignore = (
        "EDIT search_replace path=.gitignore\n<<<<<<< SEARCH\n.pytest_cache/\n=======\n"
        ".pytest_cache/\n.claude/settings.local.json\n>>>>>>> REPLACE\n"
    )
    cand = evaluate_candidate(case.ctx, case.candidate(gitignore), run_full=True)
    line = summary_line(make_receipt(case, cand))
    assert line.startswith("Grade C · ") and line.endswith(" · no code changed outside tests")
    fix = evaluate_candidate(case.ctx, case.candidate(cid="c2"), run_full=True)
    assert "no code changed" not in summary_line(make_receipt(case, fix))


def test_receipts_say_when_the_full_suite_or_slow_files_were_left_out(tmp_path: Path) -> None:
    case = zoo_case(tmp_path, repro=False)
    case.ctx.baseline.full_skipped = "the whole test suite did not finish within 120 seconds"
    case.ctx.baseline.slow_tests = ["tests/test_wester.py"]
    cand = evaluate_candidate(case.ctx, case.candidate(), run_full=False)
    gaps = make_receipt(case, cand).unverified
    assert any(
        g.startswith("Full suite not run: the whole test suite did not finish") for g in gaps
    )
    assert any("too slow to run: tests/test_wester.py" in g for g in gaps)
