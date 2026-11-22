"""`check`: grade the user's own uncommitted changes (AGENTS.md section 6). No model is involved.

base = HEAD (or --base <ref>); candidate = a snapshot of the working tree, role "user". The
cascade runs parse, scope (report only), risk (warning only), lint, fbpa (tests added in the diff,
run against the base), targeted and, unless the speed is fast, the full suite. Nothing is applied.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from qedcode.config import ProjectConfig, load_project
from qedcode.engine.budget import settings_for
from qedcode.engine.callbacks import NonInteractiveCallbacks
from qedcode.engine.lock import ProjectBusy, project_lock
from qedcode.models.ids import new_task_id
from qedcode.models.schemas import (
    Callbacks,
    Candidate,
    Event,
    Grade,
    Speed,
    TaskKind,
    TaskSpec,
)
from qedcode.project.context import related_tests
from qedcode.project.detect import detect_project
from qedcode.vcs import worktree
from qedcode.vcs.git import GitError, git, git_out, has_commits, is_git_repo, repo_root
from qedcode.vcs.snapshot import snapshot
from qedcode.verify.baseline import baseline_run
from qedcode.verify.cascade import Cancelled, CascadeContext, evaluate_candidate
from qedcode.verify.guard import changed_paths
from qedcode.verify.receipt import build_receipt, save_receipt, summary_line


class CheckOutcome(BaseModel):
    """What `check` returns to every UI (CLI, TUI, MCP)."""

    status: Literal["graded", "rejected", "no_changes", "cancelled", "busy", "error"]
    grade: Grade | None = None
    summary: str = ""
    message: str = ""
    failing_tests: list[str] = Field(default_factory=list)
    receipt_path: str | None = None
    base: str = "HEAD"
    task_id: str = ""
    candidate: Candidate | None = None
    notes: list[str] = Field(default_factory=list)


def _stage(cb: Callbacks, message: str) -> None:
    cb.on_event(Event(kind="stage", message=message))


def project_config_for(root: Path, notes: list[str]) -> ProjectConfig:
    """The confirmed project config, or the detected one (not saved) with a note."""
    saved = load_project(root)
    if saved.confirmed:
        return saved
    detected = detect_project(root)
    notes.extend(detected.notes)
    notes.append(
        f"Using the detected Python ({detected.python}). Run `qed` once in this folder to "
        "confirm the settings."
    )
    return detected.config


def _failing(candidate: Candidate) -> list[str]:
    keys: list[str] = []
    for check in candidate.checks:
        for test in check.tests:
            if test.outcome in ("failed", "error") and test.key not in keys:
                keys.append(test.key)
    return keys


def check(
    root: Path | str,
    *,
    speed: Speed = Speed.NORMAL,
    base: str = "HEAD",
    cb: Callbacks | None = None,
    allow_risky: bool = False,
) -> CheckOutcome:
    """Grade the uncommitted changes in `root` against `base`, write a receipt, apply nothing."""
    cb = cb or NonInteractiveCallbacks()
    start = Path(root)
    if not is_git_repo(start):
        return CheckOutcome(
            status="error",
            base=base,
            message="This folder is not a git repository, so there is nothing to compare with. "
            "Run `git init` and commit your work first.",
        )
    folder = repo_root(start)
    if not has_commits(folder):
        return CheckOutcome(
            status="error",
            base=base,
            message="This repository has no commits yet, so there is nothing to compare with. "
            "Make a first commit, then run `qed check` again.",
        )
    ref = (
        None
        if base.startswith("-")
        else git(folder, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}")
    )
    if ref is None or not ref.ok:
        return CheckOutcome(
            status="error",
            base=base,
            message=f"'{base}' is not a commit in this repository. Pass --base with a branch, "
            "tag or commit that exists (for example --base HEAD~1).",
        )
    base_sha = ref.stdout.strip()
    task = TaskSpec(id=new_task_id(), request="Check my changes", kind=TaskKind.OTHER, speed=speed)
    try:
        with project_lock(folder, task.id):
            return _check_locked(folder, task, base, base_sha, cb, allow_risky)
    except ProjectBusy as exc:
        return CheckOutcome(status="busy", base=base, task_id=task.id, message=str(exc))
    except GitError as exc:
        return CheckOutcome(status="error", base=base, task_id=task.id, message=str(exc))


def _check_locked(
    root: Path, task: TaskSpec, base: str, base_sha: str, cb: Callbacks, allow_risky: bool
) -> CheckOutcome:
    notes: list[str] = []
    worktree.prune(root)
    _stage(cb, "Taking a snapshot of your current files...")
    snap = snapshot(root, task.id)
    if git_out(root, "rev-parse", f"{snap}^{{tree}}") == git_out(
        root, "rev-parse", f"{base_sha}^{{tree}}"
    ):
        return CheckOutcome(
            status="no_changes",
            base=base,
            task_id=task.id,
            message=f"Your files are identical to {base}, so there is nothing to check. Make a "
            "change first, or compare with an older commit using --base.",
        )
    project = project_config_for(root, notes)
    settings = settings_for(task.speed)
    run_dir = root / ".qed" / "runs" / task.id
    changed = changed_paths(worktree.diff(root, base_sha, snap))
    _stage(cb, f"Running your tests on {base} to see what already passes...")
    base_run = baseline_run(
        root,
        base_sha,
        project,
        full=settings.baseline_full,
        related=related_tests(changed, root, project.language),
        run_dir=run_dir,
        name=f"{task.id}-base",
        cancelled=cb.cancelled,
        full_limit_s=settings.full_suite_limit_s or None,
    )
    if not base_run.baseline.runnable:
        cb.on_event(Event(kind="warning", message=base_run.baseline.reason))
        notes.append(
            f"Your tests could not run, so the grade is at most C: {base_run.baseline.reason} "
            "Run `qed doctor` to see how to fix it."
        )
    ctx = CascadeContext(
        root=root,
        base_commit=base_sha,
        baseline=base_run.baseline,
        project=project,
        task=task,
        run_dir=run_dir,
        baseline_full_seconds=base_run.full_seconds,
        on_event=cb.on_event,
        confirm=cb.confirm,
        cancelled=cb.cancelled,
        allow_risky=allow_risky,
    )
    candidate = Candidate(id="u1", role="user", parent_id=None, model="")
    _stage(cb, "Checking your changes...")
    try:
        evaluate_candidate(ctx, candidate, start_commit=snap, run_full=settings.full_on_finalist)
    except Cancelled:
        return CheckOutcome(
            status="cancelled", base=base, task_id=task.id, message="The check was cancelled."
        )
    failing = _failing(candidate)
    if candidate.grade is None:
        last = candidate.checks[-1].summary if candidate.checks else "no result"
        return CheckOutcome(
            status="rejected",
            base=base,
            task_id=task.id,
            message=f"Rejected at {candidate.failed_stage}: {last}",
            summary=f"Rejected at {candidate.failed_stage}",
            failing_tests=failing,
            candidate=candidate,
            notes=notes,
        )
    receipt = build_receipt(
        root=root,
        task=task,
        cand=candidate,
        baseline=base_run.baseline,
        project=project,
        base_commit=base_sha,
        base_ref=base,
        model_info={"backend": "none", "mode": "check"},
    )
    path = save_receipt(root, receipt)
    line = summary_line(receipt)
    cb.on_event(Event(kind="result", message=line, data={"grade": candidate.grade.value}))
    return CheckOutcome(
        status="graded",
        grade=candidate.grade,
        summary=line,
        message=line,
        failing_tests=failing,
        receipt_path=str(path),
        base=base,
        task_id=task.id,
        candidate=candidate,
        notes=notes,
    )
