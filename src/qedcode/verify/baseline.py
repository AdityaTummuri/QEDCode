"""The baseline: test outcomes on the snapshot before any change (AGENTS.md sections 3 and 8).

Run in its own worktree so the user's files are never touched. Failures here are "known
failures": they are never blamed on a candidate.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from qedcode.config import ProjectConfig
from qedcode.models.schemas import Baseline
from qedcode.project.detect import expand_test_command
from qedcode.vcs import worktree
from qedcode.verify.pytest_runner import PytestOutcome, run_pytest


@dataclass
class BaselineRun:
    """The Baseline model plus facts the controller needs but the model does not hold."""

    baseline: Baseline
    full_seconds: float | None  # how long the full suite took (None when it was not run)
    outcome: PytestOutcome | None


def baseline_run(
    root: Path | str,
    snapshot: str,
    project: ProjectConfig,
    *,
    full: bool,
    related: list[str] | None = None,
    run_dir: Path | None = None,
    name: str = "baseline",
    cancelled: Callable[[], bool] | None = None,
) -> BaselineRun:
    """Run the full suite (or only the related tests) on the snapshot and record every outcome."""
    root = Path(root)
    selection = None if full else list(related or [])
    if not full and not selection:
        return BaselineRun(
            Baseline(
                commit=snapshot,
                full=False,
                runnable=True,
                reason="No related tests were found to run before the change.",
            ),
            None,
            None,
        )
    checkout = worktree.create(root, snapshot, name)
    try:
        python = project.python or sys.executable
        outcome = run_pytest(
            checkout,
            python,
            selection,
            project.test_timeout_seconds,
            stage="full" if full else "targeted",
            run_dir=run_dir,
            command_prefix=expand_test_command(project),
            cancelled=cancelled,
        )
    finally:
        worktree.remove(root, checkout)
        shutil.rmtree(checkout, ignore_errors=True)
    runnable = outcome.status in ("ok", "failed", "no_tests", "collection_error")
    reason = ""
    if outcome.status in ("error", "timeout", "cancelled"):
        reason = outcome.check.summary
    elif outcome.status == "collection_error":
        reason = (
            f"Some test files could not be collected before the change: {outcome.check.summary}"
        )
    result = Baseline(
        commit=snapshot,
        results=outcome.as_map(),
        full=full,
        runnable=runnable,
        reason=reason,
    )
    return BaselineRun(result, outcome.duration_s if full else None, outcome)


def baseline(
    root: Path | str,
    snapshot: str,
    project: ProjectConfig,
    *,
    full: bool,
    related: list[str] | None = None,
    run_dir: Path | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Baseline:
    """Baseline test outcomes for `snapshot` (see baseline_run for the timing too)."""
    return baseline_run(
        root, snapshot, project, full=full, related=related, run_dir=run_dir, cancelled=cancelled
    ).baseline


def fails_on_base(
    root: Path | str,
    base_commit: str,
    project: ProjectConfig,
    test_path: str,
    content: str,
    *,
    run_dir: Path | None = None,
    name: str = "repro-base",
    cancelled: Callable[[], bool] | None = None,
) -> list[str] | None:
    """Which tests of a new test file FAIL on the base code (keys of failed/error tests).

    A repro test that passes on the base proves nothing, so the controller drops it. Returns None
    when pytest could not run at all. Runs in its own worktree; the user's files are untouched.
    """
    root = Path(root)
    checkout = worktree.create(root, base_commit, name)
    try:
        target = checkout / test_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="")
        outcome = run_pytest(
            checkout,
            project.python or sys.executable,
            [test_path],
            project.test_timeout_seconds,
            stage="fbpa",
            run_dir=run_dir,
            command_prefix=expand_test_command(project),
            cancelled=cancelled,
        )
    finally:
        worktree.remove(root, checkout)
        shutil.rmtree(checkout, ignore_errors=True)
    if outcome.status in ("timeout", "cancelled", "error"):
        return None
    return [r.key for r in outcome.failing]
