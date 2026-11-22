"""The verification cascade (AGENTS.md section 8): cheapest checks first, stop at the first failure.

apply -> parse -> scope -> risk -> lint -> fbpa -> targeted -> full

`evaluate_candidate` gives one candidate a fresh worktree, applies its edits, commits, diffs against
the base and runs the stages. Stages that have nothing to check (no Python files, no related tests,
no new test) are skipped: they are announced as events but not recorded, so the grade never counts
a check that did not really run. Only `verify/grade.py` turns the result into a grade.
"""

from __future__ import annotations

import ast
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from qedcode.config import ProjectConfig
from qedcode.edits.apply import apply_edits_report
from qedcode.edits.protocol import problem_text, reply_ok
from qedcode.models.schemas import (
    Baseline,
    Candidate,
    CheckResult,
    Event,
    TaskSpec,
)
from qedcode.project.context import select_targeted
from qedcode.project.paths import is_inert_path, is_js_path, is_test_file
from qedcode.vcs import worktree
from qedcode.vcs.git import GitError, git_out
from qedcode.vcs.snapshot import keep_candidate
from qedcode.verify import grade as grading
from qedcode.verify.guard import (
    FileDiff,
    count_changed_lines,
    parse_diff,
    risk_check,
    risk_findings,
    scope_check,
)
from qedcode.verify.lint import lint_check, parse_check
from qedcode.verify.pytest_runner import (
    PytestOutcome,
    failure_report,
    file_level_failure,
)
from qedcode.verify.runners import run_tests
from qedcode.verify.test_additions import (
    appended_tests_in_worktree,
    is_new_test_key,
    key_in_file,
)

DETAILS_LIMIT = 4000


class Cancelled(Exception):  # noqa: N818 - control flow, not an error condition
    """The user cancelled while a candidate was being checked."""


@dataclass
class ReproTest:
    """A test written for the task. It must fail on the base to count as a repro."""

    path: str
    content: str
    base_failures: list[str] = field(default_factory=list)  # keys that fail on the base


@dataclass
class CascadeContext:
    """Everything the cascade needs that is the same for every candidate of a task."""

    root: Path
    base_commit: str  # what diffs, lint and the baseline compare against
    baseline: Baseline
    project: ProjectConfig
    task: TaskSpec
    run_dir: Path
    baseline_full_seconds: float | None = None
    repro: ReproTest | None = None
    on_event: Callable[[Event], None] = lambda event: None
    confirm: Callable[[str], bool] = lambda prompt: False
    cancelled: Callable[[], bool] = lambda: False
    allow_risky: bool = False

    @property
    def python(self) -> str:
        return self.project.python or sys.executable


# --------------------------------------------------------------------------- small helpers


def _emit(ctx: CascadeContext, kind: str, message: str, **data) -> None:
    ctx.on_event(Event(kind=kind, message=message, data=data))  # type: ignore[arg-type]


def _check_cancel(ctx: CascadeContext) -> None:
    if ctx.cancelled():
        raise Cancelled


def _record(ctx: CascadeContext, cand: Candidate, check: CheckResult) -> bool:
    """Store a stage result, announce it, and return False when it rejects the candidate."""
    cand.checks.append(check)
    mark = "ok" if check.passed else "FAILED"
    _emit(
        ctx,
        "check",
        f"{cand.id} · {check.stage}: {check.summary}",
        candidate=cand.id,
        stage=check.stage,
        passed=check.passed,
        status=mark,
    )
    if not check.passed:
        cand.failed_stage = check.stage
        return False
    return True


def _skip(ctx: CascadeContext, cand: Candidate, stage: str, why: str) -> None:
    _emit(
        ctx,
        "check",
        f"{cand.id} · {stage}: skipped ({why})",
        candidate=cand.id,
        stage=stage,
        skipped=True,
    )


def _trim(text: str) -> str:
    return text[:DETAILS_LIMIT]


def _passed_count(out: PytestOutcome) -> int:
    return sum(1 for r in out.results if r.outcome == "passed")


def _verdict(out: PytestOutcome, baseline: Baseline, stage: str) -> tuple[CheckResult, list[str]]:
    """Judge a test run against the baseline: only NEW failures count against the candidate."""
    known = {k for k, v in baseline.results.items() if v in ("failed", "error")}
    bad = [r for r in out.failing if r.key not in known]
    ignored = len(out.failing) - len(bad)
    check = out.check.model_copy(deep=True)
    check.stage = stage  # type: ignore[assignment]
    if out.status in ("timeout", "error", "cancelled"):
        return check, [r.key for r in bad]
    if bad:
        check.passed = False
        first = bad[0]
        check.summary = (
            f"{len(bad)} test(s) fail that passed before the change; first: {first.key}"
            + (f" ({first.message})" if first.message else "")
        )[:300]
        check.details = _trim(failure_report(out.bodies, out.results, [r.key for r in bad]))
        return check, [r.key for r in bad]
    check.passed = True
    done = _passed_count(out)
    total = sum(1 for r in out.results if r.outcome in ("passed", "failed", "error"))
    note = f" ({ignored} known failure(s) ignored)" if ignored else ""
    check.summary = f"{done}/{total} tests pass{note}."[:300]
    check.details = ""
    return check, []


# --------------------------------------------------------------------------- stages


def _stage_apply(ctx: CascadeContext, cand: Candidate, wt: Path) -> bool:
    if not reply_ok(cand.reply):
        check = CheckResult(
            stage="apply",
            passed=False,
            summary=f"The reply had no usable edits: {problem_text(cand.reply)}"[:300],
            details=_trim(problem_text(cand.reply)),
        )
        return _record(ctx, cand, check)
    report = apply_edits_report(
        wt,
        cand.reply.edits,
        "coder",
        protected=ctx.project.protected,
        allow_protected=True,  # protected files are rejected by the scope stage, on the real diff
    )
    if report.errors:
        check = CheckResult(
            stage="apply",
            passed=False,
            summary=report.errors[0].split("\n")[0][:300],
            details=_trim("\n".join(report.errors)),
        )
        return _record(ctx, cand, check)
    files = ", ".join(report.changed + report.created)
    return _record(
        ctx, cand, CheckResult(stage="apply", passed=True, summary=f"Edits applied to {files}.")
    )


def _stage_risk(ctx: CascadeContext, cand: Candidate, role: str) -> bool:
    check = risk_check(cand.diff)
    if not check.needs_approval:
        return _record(ctx, cand, check)
    if role == "user":  # the user's own code: a warning, never a rejection
        check.passed = True
        check.needs_approval = False
        check.summary = "Warning: " + check.summary
        return _record(ctx, cand, check)
    findings = "; ".join(f.describe() for f in risk_findings(cand.diff)[:5])
    approved = ctx.allow_risky or ctx.confirm(
        f"This change uses risky code ({findings}). Allow it and keep checking?\n{check.details}"
    )
    if approved:
        check.passed = True
        check.summary = f"Approved: {check.summary}"[:300]
    return _record(ctx, cand, check)


def _is_test_path(path: str) -> bool:
    return is_test_file(path) or path.endswith("conftest.py")


_PYTEST_CONFIGS = ("pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml")


def _docs_are_tested(wt: Path) -> bool:
    """Does the project's pytest config run Markdown/reST files as doctests (`--doctest-glob`)?"""
    for name in _PYTEST_CONFIGS:
        try:
            if "doctest-glob" in (wt / name).read_text(encoding="utf-8", errors="replace"):
                return True
        except OSError:
            continue
    return False


def changes_code(root: Path, base_commit: str, wt: Path, files: list[FileDiff]) -> bool:
    """False when every change outside tests leaves the code the tests run unchanged.

    That is a .py file whose syntax tree is unchanged (only comments or formatting), or a file no
    test can run or read (`is_inert_path`: .gitignore, licences, docs). Such candidates pass their
    tests because nothing changed, which is no evidence. Anything we cannot compare counts.
    """
    code = [f for f in files if not _is_test_path(f.path)]
    if not code:
        return False
    docs_tested = _docs_are_tested(wt)
    for f in code:
        if is_inert_path(f.path, docs_are_tested=docs_tested):
            continue
        if f.status != "modified" or f.binary or not f.path.endswith(".py"):
            return True
        try:
            before = git_out(root, "show", f"{base_commit}:{f.path}", strip=False)
            after = (wt / f.path).read_text(encoding="utf-8")
            if ast.dump(ast.parse(before)) != ast.dump(ast.parse(after)):
                return True
        except (GitError, OSError, SyntaxError, ValueError):
            return True
    return False


def _added_test_files(
    ctx: CascadeContext, files: list[FileDiff], appended: dict[str, set[str]] | None = None
) -> list[str]:
    """New test files, plus existing test files that only gained tests (`appended`)."""
    added = {f.path for f in files if f.status == "added" and is_test_file(f.path)}
    added |= set(appended or {})
    if ctx.repro is not None:
        added.add(ctx.repro.path)
    return sorted(added)


def _counts_as_new(key: str, appended: dict[str, set[str]]) -> bool:
    """A test from an appended file proves something only if it is one of the added tests."""
    for path, names in appended.items():
        if key_in_file(key, path):
            return is_new_test_key(key, names)
    return True


def _cand_dir(ctx: CascadeContext, cand: Candidate) -> Path:
    """Each candidate's test logs and reports live apart, so a later try never overwrites them."""
    return ctx.run_dir / cand.id


def _base_failures_for(ctx: CascadeContext, wt: Path, tests: list[str], run_dir: Path) -> list[str]:
    """Which of these (new) test files' tests fail on the base? Runs them in a base worktree."""
    if ctx.repro is not None and tests == [ctx.repro.path]:
        return ctx.repro.base_failures
    base = worktree.create(ctx.root, ctx.base_commit, f"{ctx.task.id}-fbpa-base")
    try:
        for rel in tests:
            target = base / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(wt / rel, target)
        out = run_tests(
            base,
            ctx.project,
            ctx.root,
            tests,
            ctx.project.test_timeout_seconds,
            stage="fbpa",
            run_dir=run_dir / "base",
            cancelled=ctx.cancelled,
        )
    finally:
        worktree.remove(ctx.root, base)
    return [r.key for r in out.failing]


def _run_tests(
    ctx: CascadeContext, wt: Path, selection: list[str] | None, stage: str, run_dir: Path
) -> PytestOutcome:
    out = run_tests(
        wt,
        ctx.project,
        ctx.root,
        selection,
        ctx.project.test_timeout_seconds,
        stage="full" if stage == "full" else ("fbpa" if stage == "fbpa" else "targeted"),
        run_dir=run_dir,
        cancelled=ctx.cancelled,
    )
    if out.status == "cancelled":
        raise Cancelled
    return out


def _stage_fbpa(
    ctx: CascadeContext,
    cand: Candidate,
    wt: Path,
    files: list[FileDiff],
    appended: dict[str, set[str]] | None = None,
) -> bool:
    appended = appended or {}
    tests = _added_test_files(ctx, files, appended)
    if not tests:
        _skip(ctx, cand, "fbpa", "no new test")
        return True
    out = _run_tests(ctx, wt, tests, "fbpa", _cand_dir(ctx, cand))
    if out.status == "no_tests":
        check = out.check.model_copy(deep=True)
        check.passed = False
        check.summary = "The new test file contains no tests."
        return _record(ctx, cand, check)
    if not out.check.passed:
        check = out.check.model_copy(deep=True)
        check.stage = "fbpa"
        check.summary = f"The new test does not pass with the change. {check.summary}"[:300]
        return _record(ctx, cand, check)
    before = set(_base_failures_for(ctx, wt, tests, _cand_dir(ctx, cand)))
    whole_file_failed = file_level_failure(before)
    proof = [
        r
        for r in out.results
        if r.outcome == "passed"
        and (r.key in before or whole_file_failed)
        and _counts_as_new(r.key, appended)
    ]
    if not proof:
        _emit(
            ctx,
            "warning",
            f"{cand.id}: the new test already passed before the change, so it proves nothing.",
            candidate=cand.id,
        )
        return True
    check = out.check.model_copy(deep=True)
    check.tests = proof
    check.summary = f"{len(proof)} test(s) failed before the change and pass now."
    return _record(ctx, cand, check)


def _stage_tests(
    ctx: CascadeContext, cand: Candidate, wt: Path, stage: str, selection: list[str] | None
) -> bool:
    out = _run_tests(ctx, wt, selection, stage, _cand_dir(ctx, cand))
    if out.status == "no_tests":
        _skip(ctx, cand, stage, "no tests were collected")
        return True
    check, _bad = _verdict(out, ctx.baseline, stage)
    ok = _record(ctx, cand, check)
    if ok:
        cand.tests_passed = max(cand.tests_passed, _passed_count(out))
        if stage == "full":
            cand.full_suite_ran = True
    return ok


# --------------------------------------------------------------------------- the cascade


def _run_stages(ctx: CascadeContext, cand: Candidate, wt: Path, run_full: bool) -> None:
    role = "user" if cand.role == "user" else "coder"
    files = parse_diff(cand.diff)
    kept = [f for f in files if f.status != "deleted"]
    py_files = [f.path for f in kept if f.path.endswith(".py")]
    source_files = [f.path for f in kept if f.path.endswith(".py") or is_js_path(f.path)]
    _check_cancel(ctx)
    if source_files:
        if not _record(ctx, cand, parse_check(wt, source_files)):
            return
    else:
        _skip(ctx, cand, "parse", "no source files changed")
    cand.code_changed = changes_code(ctx.root, ctx.base_commit, wt, files)
    # qed check only: tests appended to an existing test file are evidence, not tampering
    appended = (
        appended_tests_in_worktree(ctx.root, ctx.base_commit, wt, files) if role == "user" else {}
    )
    _check_cancel(ctx)
    scope = scope_check(
        cand.diff,
        ctx.project,
        role,
        ctx.task.speed,
        allowed_added=[ctx.repro.path] if ctx.repro else [],
        allow_protected=ctx.task.allow_test_edits,
        appended=appended,
    )
    if not _record(ctx, cand, scope):
        return
    _check_cancel(ctx)
    if not _stage_risk(ctx, cand, role):
        return
    _check_cancel(ctx)
    if py_files:
        if not _record(ctx, cand, lint_check(wt, ctx.root, ctx.base_commit, py_files)):
            return
    else:
        _skip(
            ctx,
            cand,
            "lint",
            "no Python files changed"
            if ctx.project.language == "python"
            else "no linter runs for JavaScript or TypeScript yet",
        )
    if not ctx.baseline.runnable:
        _skip(ctx, cand, "tests", "the tests could not run before the change")
        return
    _check_cancel(ctx)
    if not _stage_fbpa(ctx, cand, wt, files, appended):
        return
    _check_cancel(ctx)
    added_tests = _added_test_files(ctx, files, appended)
    changed = [f.path for f in kept]
    selection = select_targeted(
        changed,
        wt,
        repro_files=added_tests,
        baseline_full_seconds=ctx.baseline_full_seconds,
        language=ctx.project.language,
    )
    if selection is None:
        # Nothing related was found and the suite is quick, so the whole suite runs: record it as
        # the full stage (it is one) instead of running the same tests twice.
        _stage_tests(ctx, cand, wt, "full", None)
        return
    # Files the baseline found too slow would time out here too: leave them out.
    runnable = [t for t in selection if t not in ctx.baseline.slow_tests]
    if not selection:
        _skip(ctx, cand, "targeted", "no related tests were found")
    elif not runnable:
        _skip(ctx, cand, "targeted", "every related test file is too slow to run")
    elif not _stage_tests(ctx, cand, wt, "targeted", runnable):
        return
    if run_full and not ctx.baseline.full_skipped:
        _check_cancel(ctx)
        _stage_tests(ctx, cand, wt, "full", None)


def _finish(ctx: CascadeContext, cand: Candidate) -> None:
    grade = grading.assign_grade(cand, ctx.baseline)
    if grade is None:
        message = f"{cand.id}: rejected at {cand.failed_stage}"
    else:
        message = f"{cand.id}: grade {grade.value}"
    _emit(
        ctx,
        "candidate",
        message,
        candidate=cand.id,
        grade=grade.value if grade else None,
        failed_stage=cand.failed_stage,
    )


def evaluate_candidate(
    ctx: CascadeContext,
    cand: Candidate,
    *,
    start_commit: str | None = None,
    run_full: bool = False,
) -> Candidate:
    """Check one candidate in a fresh worktree and set its checks, grade and commit.

    `start_commit` is where the worktree begins (the base for a first try; the near-miss's commit
    for a repair). The diff, lint and baseline always compare against `ctx.base_commit`. For the
    role "user" (qed check) `start_commit` is a snapshot of the working tree and nothing is applied.
    """
    cand.checks = []
    cand.failed_stage = None
    grading.reset_grade(cand)
    cand.tests_passed = 0
    cand.full_suite_ran = False
    start = start_commit or ctx.base_commit
    wt = worktree.create(ctx.root, start, f"{ctx.task.id}-{cand.id}")
    cand.worktree = str(wt)
    try:
        if cand.role != "user":
            if ctx.repro is not None:
                target = wt / ctx.repro.path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(ctx.repro.content, encoding="utf-8", newline="")
            _check_cancel(ctx)
            if not _stage_apply(ctx, cand, wt):
                _finish(ctx, cand)
                return cand
        cand.commit = worktree.commit_all(wt, f"qed candidate {cand.id}")
        keep_candidate(ctx.root, ctx.task.id, cand.id, cand.commit)
        cand.diff = worktree.diff(ctx.root, ctx.base_commit, cand.commit)
        cand.diff_lines = count_changed_lines(cand.diff)
        _run_stages(ctx, cand, wt, run_full)
        _finish(ctx, cand)
        return cand
    finally:
        worktree.remove(ctx.root, wt)
        cand.worktree = None


def run_full(ctx: CascadeContext, cand: Candidate) -> Candidate:
    """Run the full suite on a finished candidate (the finalist) and regrade it."""
    if cand.commit is None or cand.failed_stage is not None or ctx.baseline.full_skipped:
        return cand
    wt = worktree.create(ctx.root, cand.commit, f"{ctx.task.id}-{cand.id}-full")
    try:
        _check_cancel(ctx)
        _stage_tests(ctx, cand, wt, "full", None)
    finally:
        worktree.remove(ctx.root, wt)
    _finish(ctx, cand)
    return cand
