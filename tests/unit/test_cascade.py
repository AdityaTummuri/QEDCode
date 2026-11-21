from __future__ import annotations

from pathlib import Path

import pytest

from qedcode.models.schemas import Grade, Speed
from qedcode.vcs import worktree
from qedcode.verify.cascade import (
    Cancelled,
    ReproTest,
    changes_code,
    evaluate_candidate,
    run_full,
)
from qedcode.verify.guard import FileDiff
from tests.conftest import run_git
from tests.zoo_helpers import REPRO_PATH, zoo_case

FENCE = "```"


def sr(path: str, old: str, new: str) -> str:
    return (
        f"EDIT search_replace path={path}\n<<<<<<< SEARCH\n{old}\n=======\n{new}\n>>>>>>> REPLACE\n"
    )


PAGES = "src/shop/pages.py"


# ------------------------------------------------------------------ the happy paths


def test_a_good_fix_with_a_repro_test_reaches_grade_a(tmp_path: Path) -> None:
    case = zoo_case(tmp_path)
    assert case.ctx.repro and case.ctx.repro.base_failures  # the repro fails on the buggy code
    cand = evaluate_candidate(case.ctx, case.candidate(), run_full=True)
    assert cand.grade is Grade.A, [(c.stage, c.passed, c.summary) for c in cand.checks]
    assert [c.stage for c in cand.checks] == [
        "apply",
        "parse",
        "scope",
        "risk",
        "lint",
        "fbpa",
        "targeted",
        "full",
    ]
    assert all(c.passed for c in cand.checks)
    assert cand.failed_stage is None and cand.full_suite_ran and cand.tests_passed >= 5
    assert "src/shop/pages.py" in cand.diff and REPRO_PATH in cand.diff
    assert cand.diff_lines >= 3 and cand.commit
    fbpa = next(c for c in cand.checks if c.stage == "fbpa")
    assert len(fbpa.tests) == 2 and all(t.outcome == "passed" for t in fbpa.tests)
    assert "failed before the change and pass now" in fbpa.summary
    assert case.stages() == [(c.stage, True) for c in cand.checks]


def test_a_good_fix_without_a_repro_test_is_grade_b(tmp_path: Path) -> None:
    case = zoo_case(tmp_path, repro=False)
    cand = evaluate_candidate(case.ctx, case.candidate(), run_full=True)
    assert cand.grade is Grade.B and cand.full_suite_ran
    assert "fbpa" not in [c.stage for c in cand.checks]


def test_without_the_full_suite_a_proven_fix_is_grade_b(tmp_path: Path) -> None:
    case = zoo_case(tmp_path)
    cand = evaluate_candidate(case.ctx, case.candidate(), run_full=False)
    assert cand.grade is Grade.B and not cand.full_suite_ran
    assert [c.stage for c in cand.checks][-1] == "targeted"


def test_run_full_regrades_a_finished_candidate_from_b_to_a(tmp_path: Path) -> None:
    case = zoo_case(tmp_path)
    cand = evaluate_candidate(case.ctx, case.candidate(), run_full=False)
    assert cand.grade is Grade.B
    run_full(case.ctx, cand)
    assert cand.grade is Grade.A and cand.full_suite_ran
    assert cand.checks[-1].stage == "full" and cand.checks[-1].passed
    assert worktree.list_worktrees(case.project) == []


def test_worktrees_are_removed_and_the_commit_is_kept(tmp_path: Path) -> None:
    case = zoo_case(tmp_path)
    cand = evaluate_candidate(case.ctx, case.candidate())
    assert worktree.list_worktrees(case.project) == [] and cand.worktree is None
    ref = f"refs/qed/candidates/{case.ctx.task.id}/c1"
    assert run_git(case.project, "rev-parse", ref).strip() == cand.commit
    assert (
        run_git(case.project, "status", "--porcelain").strip() == ""
    )  # the user's tree is untouched


def test_events_are_emitted_after_every_stage(tmp_path: Path) -> None:
    case = zoo_case(tmp_path)
    evaluate_candidate(case.ctx, case.candidate(), run_full=True)
    checks = [e for e in case.events if e.kind == "check"]
    assert [e.data["stage"] for e in checks] == [
        "apply",
        "parse",
        "scope",
        "risk",
        "lint",
        "fbpa",
        "targeted",
        "full",
    ]
    assert all(e.message.startswith("c1 · ") for e in checks)
    last = case.events[-1]
    assert last.kind == "candidate" and last.data["grade"] == "A" and last.message == "c1: grade A"


# ------------------------------------------------------------------ rejection at each stage


def test_a_reply_with_no_edits_fails_at_apply(tmp_path: Path) -> None:
    case = zoo_case(tmp_path)
    cand = evaluate_candidate(case.ctx, case.candidate("I think the bug is in paginate."))
    assert cand.failed_stage == "apply" and cand.grade is None
    assert (
        "no usable edits" in cand.checks[0].summary
        and "no EDIT blocks found" in cand.checks[0].details
    )
    assert cand.commit is None and worktree.list_worktrees(case.project) == []
    assert case.events[-1].message == "c1: rejected at apply"


def test_an_edit_that_cannot_be_applied_fails_at_apply_with_the_hint(tmp_path: Path) -> None:
    case = zoo_case(tmp_path)
    bad = f"EDIT replace_symbol path={PAGES} symbol=paginat\n{FENCE}python\ndef paginat(): pass\n{FENCE}\n"
    cand = evaluate_candidate(case.ctx, case.candidate(bad))
    assert cand.failed_stage == "apply"
    assert "symbol not found" in cand.checks[0].summary
    assert "paginate" in cand.checks[0].details and "page_count" in cand.checks[0].details


def test_broken_syntax_fails_at_parse(tmp_path: Path) -> None:
    case = zoo_case(tmp_path)
    text = sr(PAGES, "    end = start + size - 1", "    end = start + size -")
    cand = evaluate_candidate(case.ctx, case.candidate(text))
    assert cand.failed_stage == "parse" and cand.grade is None
    assert "pages.py, line" in cand.checks[-1].summary
    assert [c.stage for c in cand.checks] == [
        "apply",
        "parse",
    ]  # the cascade stops at the first failure


def test_editing_a_test_file_is_rejected_at_scope_with_a_clear_message(tmp_path: Path) -> None:
    case = zoo_case(tmp_path)
    text = sr("tests/test_pages.py", "assert paginate([], 1, 10) == []", "assert True")
    cand = evaluate_candidate(case.ctx, case.candidate(text))
    assert cand.failed_stage == "scope" and cand.grade is None
    assert "tests/test_pages.py is a protected file" in cand.checks[-1].summary
    assert "Change the code under test instead" in cand.checks[-1].details


def test_a_coder_creating_a_test_file_is_rejected_at_scope(tmp_path: Path) -> None:
    case = zoo_case(tmp_path)
    text = (
        case.edits_text
        + f"\nEDIT create_file path=tests/test_extra.py\n{FENCE}python\ndef test_x():\n    assert True\n{FENCE}\n"
    )
    cand = evaluate_candidate(case.ctx, case.candidate(text))
    assert cand.failed_stage == "scope"


def test_deleting_a_file_is_rejected_at_scope(tmp_path: Path) -> None:
    case = zoo_case(tmp_path)
    (case.project / "src" / "shop" / "extra.py").write_text("E = 1\n")
    run_git(case.project, "add", "-A")
    run_git(case.project, "commit", "-q", "-m", "extra")
    from qedcode.vcs.snapshot import snapshot

    case.ctx.base_commit = snapshot(case.project, "t-delete")
    # a reply cannot delete files, but an oversized change is the other scope failure we can script
    big = sr(
        PAGES,
        "    end = start + size - 1",
        "    end = start + size\n" + "\n".join(f"    v{i} = {i}" for i in range(400)),
    )
    cand = evaluate_candidate(case.ctx, case.candidate(big))
    assert cand.failed_stage == "scope" and "limit at this speed is 300" in cand.checks[-1].details


def test_risky_code_is_rejected_when_nobody_approves_it(tmp_path: Path) -> None:
    case = zoo_case(tmp_path)
    risky = sr(PAGES, "    end = start + size - 1", "    import subprocess\n    end = start + size")
    cand = evaluate_candidate(case.ctx, case.candidate(risky))
    assert cand.failed_stage == "risk" and cand.checks[-1].needs_approval
    assert "subprocess in src/shop/pages.py" in cand.checks[-1].summary


def test_a_confirmed_risk_passes_but_caps_the_grade_at_b(tmp_path: Path) -> None:
    asked: list[str] = []
    case = zoo_case(tmp_path, confirm=lambda prompt: asked.append(prompt) or True)
    risky = sr(PAGES, "    end = start + size - 1", "    import subprocess\n    end = start + size")
    cand = evaluate_candidate(case.ctx, case.candidate(risky), run_full=True)
    assert asked and "subprocess in src/shop/pages.py" in asked[0]
    risk = next(c for c in cand.checks if c.stage == "risk")
    assert risk.passed and risk.needs_approval and risk.summary.startswith("Approved:")
    assert all(c.passed for c in cand.checks) and cand.full_suite_ran
    assert cand.grade is Grade.B  # would have been A: proof is weaker after a risky approval


def test_allow_risky_skips_the_question(tmp_path: Path) -> None:
    case = zoo_case(
        tmp_path,
        allow_risky=True,
        confirm=lambda prompt: pytest.fail("must not ask when --allow-risky is set"),
    )
    risky = sr(PAGES, "    end = start + size - 1", "    import subprocess\n    end = start + size")
    assert evaluate_candidate(case.ctx, case.candidate(risky)).failed_stage is None


def test_a_new_lint_error_is_rejected_at_lint(tmp_path: Path) -> None:
    case = zoo_case(tmp_path)
    text = sr(PAGES, "    end = start + size - 1", "    end = start + size + undefined_thing")
    cand = evaluate_candidate(case.ctx, case.candidate(text))
    assert cand.failed_stage == "lint" and "F821" in cand.checks[-1].summary


def test_the_repro_test_still_failing_means_rejection_at_fbpa(tmp_path: Path) -> None:
    case = zoo_case(tmp_path)
    no_fix = sr(
        PAGES,
        '    """Return the items on a 1-based page of the given size."""',
        '    """Return a page of items."""',
    )
    cand = evaluate_candidate(case.ctx, case.candidate(no_fix))
    assert cand.failed_stage == "fbpa" and cand.grade is None
    last = cand.checks[-1]
    assert "does not pass with the change" in last.summary
    assert "assert" in last.details and "paginate" in last.details  # the repairer gets the failure


def test_breaking_an_existing_test_is_rejected_at_targeted(tmp_path: Path) -> None:
    case = zoo_case(tmp_path)
    breaks = case.edits_text + sr(
        PAGES, "    return (total + size - 1) // size", "    return total // size"
    )
    cand = evaluate_candidate(case.ctx, case.candidate(breaks))
    assert cand.failed_stage == "targeted" and cand.grade is None
    last = cand.checks[-1]
    assert "fail that passed before the change" in last.summary
    assert (
        "test_page_count_rounds_up" in last.summary and "test_page_count_rounds_up" in last.details
    )


def test_a_regression_outside_the_targeted_tests_is_caught_by_the_full_suite(
    tmp_path: Path,
) -> None:
    case = zoo_case(tmp_path, "002_discount_threshold", repro=False)
    other = case.project / "tests" / "test_other.py"  # a test unrelated by name or import
    other.write_text(
        "import store.pricing as p\n\ndef test_cart():\n    assert p.cart_total([10, 20]) == 30\n"
    )
    run_git(case.project, "add", "-A")
    run_git(case.project, "commit", "-q", "-m", "more tests")
    from qedcode.vcs.snapshot import snapshot
    from qedcode.verify.baseline import baseline_run

    case.ctx.base_commit = snapshot(case.project, "t-reg")
    base = baseline_run(
        case.project, case.ctx.base_commit, case.ctx.project, full=True, run_dir=case.ctx.run_dir
    )
    case.ctx.baseline = base.baseline
    breaker = sr(
        "src/store/pricing.py",
        "    return apply_discount(sum(prices))",
        "    return sum(prices) + 1",
    )
    cand = evaluate_candidate(case.ctx, case.candidate(breaker), run_full=True)
    assert cand.failed_stage in ("targeted", "full")
    assert "fail that passed before" in cand.checks[-1].summary


# ------------------------------------------------------------------ known failures, repro quality, baseline


def test_known_failures_are_never_blamed_on_the_candidate(tmp_path: Path) -> None:
    case = zoo_case(tmp_path, repro=False)
    (case.project / "tests" / "test_known_bad.py").write_text(
        "def test_always_fails():\n    assert False\n"
    )
    run_git(case.project, "add", "-A")
    run_git(case.project, "commit", "-q", "-m", "a failing test")
    from qedcode.vcs.snapshot import snapshot
    from qedcode.verify.baseline import baseline_run

    case.ctx.base_commit = snapshot(case.project, "t-known")
    case.ctx.baseline = baseline_run(
        case.project, case.ctx.base_commit, case.ctx.project, full=True, run_dir=case.ctx.run_dir
    ).baseline
    assert "tests.test_known_bad::test_always_fails" in case.ctx.baseline.results
    cand = evaluate_candidate(case.ctx, case.candidate(), run_full=True)
    assert cand.grade is Grade.B and cand.failed_stage is None
    assert "known failure" in cand.checks[-1].summary


def test_a_repro_that_already_passes_on_the_base_proves_nothing(tmp_path: Path) -> None:
    case = zoo_case(tmp_path)
    case.ctx.repro = ReproTest(
        REPRO_PATH, case.hidden_text, base_failures=[]
    )  # nothing failed before
    cand = evaluate_candidate(case.ctx, case.candidate(), run_full=True)
    assert cand.grade is Grade.B and "fbpa" not in [c.stage for c in cand.checks]
    assert any(e.kind == "warning" and "proves nothing" in e.message for e in case.events)


def test_a_repro_file_with_no_tests_is_rejected(tmp_path: Path) -> None:
    case = zoo_case(tmp_path)
    case.ctx.repro = ReproTest(REPRO_PATH, "X = 1\n", base_failures=[])
    cand = evaluate_candidate(case.ctx, case.candidate())
    assert cand.failed_stage == "fbpa" and "contains no tests" in cand.checks[-1].summary


def test_when_pytest_could_not_run_before_the_change_the_best_grade_is_c(tmp_path: Path) -> None:
    case = zoo_case(tmp_path, repro=False)
    case.ctx.baseline.runnable = False
    case.ctx.baseline.reason = "pytest is not installed"
    cand = evaluate_candidate(case.ctx, case.candidate(), run_full=True)
    assert cand.grade is Grade.C and cand.tests_passed == 0
    assert [c.stage for c in cand.checks] == ["apply", "parse", "scope", "risk", "lint"]


def test_nothing_checkable_is_grade_d(tmp_path: Path) -> None:
    case = zoo_case(tmp_path, repro=False)
    case.ctx.baseline.runnable = False
    note = "EDIT create_file path=NOTES.md\n```\nhello\n```\n"
    cand = evaluate_candidate(case.ctx, case.candidate(note))
    assert cand.grade is Grade.D
    skipped = [e.message for e in case.events if "skipped" in e.message]
    assert any("parse" in m for m in skipped) and any("lint" in m for m in skipped)


def test_no_related_tests_and_a_slow_baseline_means_no_targeted_stage(tmp_path: Path) -> None:
    case = zoo_case(tmp_path, repro=False)
    case.ctx.baseline_full_seconds = 600.0  # slow suite: do not fall back to running everything
    orphan = "EDIT create_file path=src/shop/orphan.py\n```python\nO = 1\n```\n"
    cand = evaluate_candidate(case.ctx, case.candidate(orphan))
    assert "targeted" not in [c.stage for c in cand.checks]
    assert cand.grade is Grade.C  # static checks passed, nothing tested


def test_no_related_tests_and_a_quick_suite_runs_it_once_as_the_full_stage(
    tmp_path: Path,
) -> None:
    """The fallback "run everything" IS the full suite: record it as such and never run it twice."""
    case = zoo_case(tmp_path, repro=False)
    case.ctx.baseline_full_seconds = 1.0
    orphan = "EDIT create_file path=src/shop/orphan.py\n```python\nO = 1\n```\n"
    cand = evaluate_candidate(case.ctx, case.candidate(orphan), run_full=True)
    stages = [c.stage for c in cand.checks]
    assert stages.count("full") == 1 and "targeted" not in stages
    assert cand.full_suite_ran and cand.tests_passed > 0
    assert cand.grade is Grade.B  # tested, no fail-before-pass-after proof


def test_each_candidate_keeps_its_own_test_log(tmp_path: Path) -> None:
    case = zoo_case(tmp_path)
    first = evaluate_candidate(case.ctx, case.candidate())
    second = case.candidate()
    second.id = "c2"
    evaluate_candidate(case.ctx, second)
    logs = {c.log_path for c in [*first.checks, *second.checks] if c.log_path}
    assert any("/c1/" in p.replace("\\", "/") for p in logs)
    assert any("/c2/" in p.replace("\\", "/") for p in logs)
    assert all(Path(p).is_file() for p in logs)


# ------------------------------------------------------------------ cancellation and speed


def test_cancelling_stops_the_cascade_and_cleans_up(tmp_path: Path) -> None:
    case = zoo_case(tmp_path)
    flag = {"stop": False}

    def on_event(event) -> None:
        case.events.append(event)
        if event.kind == "check" and event.data.get("stage") == "parse":
            flag["stop"] = True

    case.ctx.on_event = on_event
    case.ctx.cancelled = lambda: flag["stop"]
    with pytest.raises(Cancelled):
        evaluate_candidate(case.ctx, case.candidate(), run_full=True)
    assert worktree.list_worktrees(case.project) == []
    assert not any(e.data.get("stage") == "scope" for e in case.events)


def test_the_size_limit_follows_the_speed_in_the_cascade(tmp_path: Path) -> None:
    padded = sr(
        PAGES,
        "    end = start + size - 1",
        "    end = start + size\n" + "\n".join(f"    v{i} = {i}" for i in range(250)),
    )
    fast = zoo_case(tmp_path / "fast", speed=Speed.FAST, repro=False, full_baseline=False)
    assert evaluate_candidate(fast.ctx, fast.candidate(padded)).failed_stage == "scope"
    sure = zoo_case(tmp_path / "sure", speed=Speed.SURE, repro=False)
    assert evaluate_candidate(sure.ctx, sure.candidate(padded)).failed_stage != "scope"


def test_repair_candidates_start_from_the_near_miss_commit(tmp_path: Path) -> None:
    case = zoo_case(tmp_path)
    near = evaluate_candidate(
        case.ctx,
        case.candidate(
            sr(PAGES, "    end = start + size - 1", "    end = start + size - 1  # almost")
        ),
        run_full=False,
    )
    assert near.failed_stage == "fbpa"
    fix = sr(PAGES, "    end = start + size - 1  # almost", "    end = start + size")
    repair = case.candidate(fix, cid="r1", role="repairer")
    repair.parent_id = near.id
    evaluate_candidate(case.ctx, repair, start_commit=near.commit, run_full=True)
    assert repair.grade is Grade.A
    assert "# almost" not in repair.diff  # the diff is against the snapshot, not the near-miss
    assert repair.diff.count("+    end = start + size\n") == 1


COMMENT_ONLY = """PLAN:
- add a note

EDIT search_replace path=src/shop/pages.py
<<<<<<< SEARCH
    end = start + size - 1
=======
    # end of the page
    end = start + size - 1
>>>>>>> REPLACE
"""


def test_a_comment_only_change_is_grade_c_not_b(tmp_path: Path) -> None:
    """Its tests pass only because nothing changed; that is no evidence for the task."""
    case = zoo_case(tmp_path, repro=False)
    cand = evaluate_candidate(case.ctx, case.candidate(COMMENT_ONLY), run_full=True)
    assert cand.code_changed is False
    assert cand.grade is Grade.C


def test_a_real_fix_counts_as_a_code_change(tmp_path: Path) -> None:
    case = zoo_case(tmp_path, repro=False)
    cand = evaluate_candidate(case.ctx, case.candidate(), run_full=True)
    assert cand.code_changed is True and cand.grade is Grade.B


GITIGNORE_ONLY = """PLAN:
- ignore a local file

EDIT search_replace path=.gitignore
<<<<<<< SEARCH
.pytest_cache/
=======
.pytest_cache/
.claude/settings.local.json
>>>>>>> REPLACE
"""

DATA_FILE = """PLAN:
- add a data file

EDIT create_file path=src/shop/sizes.json
```json
{"default": 10}
```
"""


def test_a_gitignore_only_change_is_grade_c_not_b(tmp_path: Path) -> None:
    """No test reads .gitignore, so passing tests are no evidence for the change."""
    case = zoo_case(tmp_path, repro=False)
    cand = evaluate_candidate(case.ctx, case.candidate(GITIGNORE_ONLY), run_full=True)
    assert cand.failed_stage is None and cand.tests_passed > 0  # C from the cap, not "no tests"
    assert cand.code_changed is False
    assert cand.grade is Grade.C


def test_a_gitignore_change_next_to_a_real_fix_keeps_grade_b(tmp_path: Path) -> None:
    case = zoo_case(tmp_path, repro=False)
    both = case.edits_text + "\n" + GITIGNORE_ONLY.split("\n\n", 1)[1]
    cand = evaluate_candidate(case.ctx, case.candidate(both), run_full=True)
    assert cand.code_changed is True and cand.grade is Grade.B


def test_a_data_file_counts_as_a_code_change(tmp_path: Path) -> None:
    """Code may read JSON, YAML or templates, so anything off the inert list still counts."""
    case = zoo_case(tmp_path, repro=False)
    cand = evaluate_candidate(case.ctx, case.candidate(DATA_FILE), run_full=True)
    assert cand.failed_stage is None
    assert cand.code_changed is True


def test_docs_count_as_code_when_pytest_runs_them_as_doctests(tmp_path: Path) -> None:
    readme = [FileDiff(path="README.md")]
    assert changes_code(tmp_path, "HEAD", tmp_path, readme) is False
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = --doctest-glob='*.md'\n")
    assert changes_code(tmp_path, "HEAD", tmp_path, readme) is True
    assert changes_code(tmp_path, "HEAD", tmp_path, [FileDiff(path=".gitignore")]) is False
