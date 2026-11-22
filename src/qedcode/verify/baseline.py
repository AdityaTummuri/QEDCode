"""The baseline: test outcomes on the snapshot before any change (AGENTS.md sections 3 and 8).

Run in its own worktree so the user's files are never touched. Failures here are "known
failures": they are never blamed on a candidate.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from qedcode.config import ProjectConfig
from qedcode.models.schemas import Baseline
from qedcode.vcs import worktree
from qedcode.vcs.git import git
from qedcode.verify.pytest_runner import PytestOutcome
from qedcode.verify.runners import run_tests


@dataclass
class BaselineRun:
    """The Baseline model plus facts the controller needs but the model does not hold."""

    baseline: Baseline
    full_seconds: float | None  # how long the full suite took (None when it was not run)
    outcome: PytestOutcome | None


def _existing_in_commit(root: Path, commit: str, paths: list[str]) -> list[str]:
    """Only the paths that exist in `commit` (new files in the working tree are left out)."""
    return [p for p in paths if git(root, "cat-file", "-e", f"{commit}:{p}").ok]


# Where a project remembers that its whole suite did not finish before the change.
TOO_SLOW_FILE = Path(".qed") / "full_suite_too_slow.json"


def _known_too_slow(root: Path, limit: int) -> int | None:
    """The limit an earlier run already saw the full suite exceed (when it is at least `limit`)."""
    try:
        seen = int(json.loads((root / TOO_SLOW_FILE).read_text(encoding="utf-8"))["limit_s"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return seen if seen >= limit else None


def _remember_too_slow(root: Path, limit: int) -> None:
    try:
        path = root / TOO_SLOW_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"limit_s": limit}) + "\n", encoding="utf-8")
    except OSError:
        pass  # only a speed-up for the next run


# A related test file that takes longer than this alone is too slow to run on every try.
RELATED_FILE_LIMIT_S = 60


def _runnable(outcome: PytestOutcome) -> bool:
    return outcome.status in ("ok", "failed", "no_tests", "collection_error")


def _reason(outcome: PytestOutcome) -> str:
    if outcome.status in ("error", "timeout", "cancelled"):
        return outcome.check.summary
    if outcome.status == "collection_error":
        return f"Some test files could not be collected before the change: {outcome.check.summary}"
    return ""


def _related_one_by_one(
    checkout: Path,
    root: Path,
    project: ProjectConfig,
    files: list[str],
    run_dir: Path | None,
    cancelled: Callable[[], bool] | None,
) -> tuple[PytestOutcome | None, dict[str, str], list[str]]:
    """Run each related test file on its own with a per-file limit.

    Returns (an outcome that stops the baseline, or None; merged results; slow files). A file over
    the limit is left out (it would time out every candidate's run too) instead of making the whole
    baseline fail, which is what one slow file does to a single combined run.
    """
    limit = min(project.test_timeout_seconds, RELATED_FILE_LIMIT_S)
    results: dict[str, str] = {}
    slow: list[str] = []
    for path in files:
        outcome = run_tests(
            checkout,
            project,
            root,
            [path],
            limit,
            stage="targeted",
            run_dir=run_dir,
            cancelled=cancelled,
        )
        if outcome.status == "cancelled" or outcome.status == "error":
            return outcome, results, slow
        if outcome.status == "timeout":
            slow.append(path)
            continue
        results.update(outcome.as_map())
    return None, results, slow


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
    full_limit_s: float | None = None,
) -> BaselineRun:
    """Run the full suite (or only the related tests) on the snapshot and record every outcome.

    The full suite gets `full_limit_s` (default: the project's test timeout). If it does not finish
    in time it is marked too slow (`Baseline.full_skipped`) and the related tests run instead, one
    file at a time with a per-file limit; files over that limit are listed in `Baseline.slow_tests`
    and left out of every later run. With no usable related tests the baseline is not runnable.
    """
    root = Path(root)
    selection = _existing_in_commit(root, snapshot, related or [])
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
    limit = project.test_timeout_seconds
    if full_limit_s is not None:
        limit = int(min(limit, full_limit_s)) or 1
    # An earlier run in this project already saw the whole suite exceed this limit: go straight
    # to the related tests instead of waiting for the same timeout again.
    seen = _known_too_slow(root, limit) if full and selection else None
    checkout = worktree.create(root, snapshot, name)
    try:
        if full and seen is not None:
            full_skipped = (
                f"the whole test suite did not finish within {seen} seconds before the change "
                "(measured on an earlier run in this project)"
            )
        elif full:
            outcome = run_tests(
                checkout,
                project,
                root,
                None,
                limit,
                stage="full",
                run_dir=run_dir,
                cancelled=cancelled,
            )
            if outcome.status != "timeout" or not selection:
                result = Baseline(
                    commit=snapshot,
                    results=outcome.as_map(),
                    full=True,
                    runnable=_runnable(outcome),
                    reason=_reason(outcome),
                )
                return BaselineRun(result, outcome.duration_s, outcome)
            _remember_too_slow(root, limit)
            full_skipped = (
                f"the whole test suite did not finish within {limit} seconds before the change"
            )
        else:
            full_skipped = ""
        stop, results, slow = _related_one_by_one(
            checkout, root, project, selection, run_dir, cancelled
        )
    finally:
        worktree.remove(root, checkout)
        shutil.rmtree(checkout, ignore_errors=True)
    if stop is not None:
        return BaselineRun(
            Baseline(
                commit=snapshot,
                results=results,
                full=False,
                runnable=False,
                reason=_reason(stop),
                full_skipped=full_skipped,
                slow_tests=slow,
            ),
            None,
            stop,
        )
    usable = [p for p in selection if p not in slow]
    reason = ""
    if not usable:
        limit = min(project.test_timeout_seconds, RELATED_FILE_LIMIT_S)
        reason = f"Every related test file took longer than {limit} seconds, so none can run."
    return BaselineRun(
        Baseline(
            commit=snapshot,
            results=results,
            full=False,
            runnable=bool(usable),
            reason=reason,
            full_skipped=full_skipped,
            slow_tests=slow,
        ),
        None,
        None,
    )


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
        outcome = run_tests(
            checkout,
            project,
            root,
            [test_path],
            project.test_timeout_seconds,
            stage="fbpa",
            run_dir=run_dir,
            cancelled=cancelled,
        )
    finally:
        worktree.remove(root, checkout)
        shutil.rmtree(checkout, ignore_errors=True)
    if outcome.status in ("timeout", "cancelled", "error"):
        return None
    return [r.key for r in outcome.failing]
