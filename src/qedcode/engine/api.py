"""The public engine API: the only thing the TUI, the CLI and the MCP server call.

solve | check | verify | apply | undo | status. Progress goes out as Event objects through a
callback. This module only wires: the logic lives in the modules it calls.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from qedcode.config import GlobalConfig, load_global
from qedcode.engine.callbacks import NonInteractiveCallbacks
from qedcode.engine.check import CheckOutcome
from qedcode.engine.check import check as _check
from qedcode.engine.lock import lock_holder
from qedcode.engine.solve import read_last_result
from qedcode.engine.solve import solve as _solve
from qedcode.models.schemas import Callbacks, Grade, Mode, SolveResult, Speed, TaskSpec
from qedcode.project.memory import Lesson, Memory
from qedcode.runtime.client import ModelClient, ModelError, make_client
from qedcode.runtime.quiet import pause_seconds
from qedcode.vcs.git import GitError, has_commits, is_git_repo, repo_root
from qedcode.vcs.patch import PatchRefused, apply_to_user, commit_applied, undo_last
from qedcode.verify.receipt import load_receipt, receipt_path
from qedcode.verify.verify import VerifyResult, verify_receipt


class ApplyOutcome(BaseModel):
    """The result of `apply` or `undo`."""

    status: Literal["applied", "undone", "refused", "needs_confirmation", "error"]
    message: str = ""
    task_id: str = ""
    files: list[str] = Field(default_factory=list)
    grade: Grade | None = None


def resolve_root(path: Path | str | None = None) -> Path:
    """The project root: the git top-level folder of `path` (default: the current folder)."""
    start = Path(path) if path is not None else Path.cwd()
    return repo_root(start) if is_git_repo(start) else start


def verify(receipt_path_: Path | str, root: Path | str | None = None) -> VerifyResult:
    """Re-run a receipt's checks inside the repository and report VERIFIED or the mismatches."""
    base = Path(root) if root is not None else resolve_root()
    return verify_receipt(receipt_path_, base)


def check(
    root: Path | str | None = None,
    *,
    speed: Speed = Speed.NORMAL,
    base: str = "HEAD",
    cb: Callbacks | None = None,
    allow_risky: bool = False,
) -> CheckOutcome:
    """Grade the uncommitted changes in the project against `base` (no model, nothing applied)."""
    folder = Path(root) if root is not None else Path.cwd()
    return _check(folder, speed=speed, base=base, cb=cb, allow_risky=allow_risky)


def _error_result(request: str, speed: Speed, message: str) -> SolveResult:
    return SolveResult(
        status="error", task=TaskSpec(id="t-none", request=request, speed=speed), message=message
    )


def solve(
    root: Path | str | None = None,
    request: str = "",
    *,
    speed: Speed | None = None,
    mode: Mode | None = None,
    cb: Callbacks | None = None,
    config: GlobalConfig | None = None,
    client: ModelClient | None = None,
    helper_client: ModelClient | None = None,
    allow_risky: bool = False,
    allow_test_edits: bool = False,
    apply: bool = False,
    resume: SolveResult | None = None,
    given_repro: tuple[str, str] | None = None,
) -> SolveResult:
    """Fix or build something: several tries, deterministic checks, one proven result.

    Nothing touches the user's files unless `apply` is set (and the grade is A or B).
    """
    cfg = config or load_global()
    chosen_speed = speed or cfg.engine.speed
    chosen_mode = mode or cfg.engine.mode
    cb = cb or NonInteractiveCallbacks()
    start = Path(root) if root is not None else Path.cwd()
    if not request.strip():
        return _error_result(
            request,
            chosen_speed,
            'Say what you want done, for example: qed solve "fix the login bug".',
        )
    if not is_git_repo(start):
        return _error_result(
            request,
            chosen_speed,
            "This folder is not a git repository. Run `qed` here once (it offers to set one up) "
            "or `git init` and commit, then try again.",
        )
    folder = repo_root(start)
    if not has_commits(folder):
        return _error_result(
            request, chosen_speed, "This repository has no commits yet. Make a first commit first."
        )
    try:
        main = client or make_client(cfg, "main")
    except ModelError as exc:
        return _error_result(request, chosen_speed, str(exc))

    def helper_factory() -> ModelClient:
        return helper_client or make_client(cfg, "helper")

    use_helper = chosen_mode is Mode.MIX and (helper_client is not None or cfg.helper.enabled)
    result = _solve(
        folder,
        request,
        chosen_speed,
        chosen_mode,
        cb,
        client=main,
        helper_factory=helper_factory if use_helper else None,
        helper_preapproved=not cfg.helper.ask_before_use,
        allow_risky=allow_risky,
        allow_test_edits=allow_test_edits,
        resume=resume,
        learn=cfg.engine.learn,
        pause_s=pause_seconds(cfg),
        given_repro=given_repro,
    )
    if apply and result.status == "done" and result.best is not None:
        if result.best.grade in (Grade.A, Grade.B):
            outcome = apply_result(folder, result.task.id, learn=cfg.engine.learn)
            result.message = f"{result.message}. {outcome.message}".strip()
        else:
            grade = result.best.grade.value if result.best.grade else "?"
            result.message += (
                f". Not applied because the grade is {grade}: review the change, "
                f"then run `qed apply {result.task.id}`."
            )
    return result


def apply_result(
    root: Path | str | None = None,
    task_id: str | None = None,
    *,
    allow_low_grade: bool = False,
    learn: bool | None = None,
    commit: bool = False,
) -> ApplyOutcome:
    """Apply a finished result to the user's files (the only way files are ever changed).

    `learn` says whether to remember an A/B fix for next time (default: the saved setting).
    `commit` also commits the patch's files with the receipt as a git note (AGENTS.md 15.10)."""
    folder = resolve_root(root)
    last = read_last_result(folder) or {}
    wanted = task_id or str(last.get("task_id", ""))
    if not wanted:
        return ApplyOutcome(
            status="error",
            message="There is no finished result to apply. Run `qed solve` first.",
        )
    path = receipt_path(folder, wanted)
    if not path.exists():
        return ApplyOutcome(
            status="error",
            task_id=wanted,
            message=f"There is no saved result for task {wanted}. Run `qed solve` first.",
        )
    try:
        receipt = load_receipt(path)
    except ValueError as exc:
        return ApplyOutcome(status="error", task_id=wanted, message=str(exc))
    if receipt.grade in (Grade.C, Grade.D) and not allow_low_grade:
        return ApplyOutcome(
            status="needs_confirmation",
            task_id=wanted,
            grade=receipt.grade,
            message=f"This change has grade {receipt.grade.value}: it is not backed by tests. "
            "Read the diff first, then confirm to apply it.",
        )
    try:
        done = apply_to_user(folder, receipt.base_commit, receipt.patch, task_id=wanted)
    except PatchRefused as exc:
        return ApplyOutcome(status="refused", task_id=wanted, files=exc.files, message=str(exc))
    except GitError as exc:
        return ApplyOutcome(status="error", task_id=wanted, message=str(exc))
    remembered = False
    if load_global().engine.learn if learn is None else learn:
        remembered = Memory(folder).record(receipt)
    note = " QEDCode will remember this fix." if remembered else ""
    if commit:
        try:
            sha = commit_applied(
                folder, done.files, commit_message(receipt, path), path.read_text(encoding="utf-8")
            )
        except PatchRefused as exc:
            return ApplyOutcome(
                status="error",
                task_id=wanted,
                files=exc.files,
                grade=receipt.grade,
                message=str(exc),
            )
        return ApplyOutcome(
            status="applied",
            task_id=wanted,
            grade=receipt.grade,
            files=done.files,
            message=f"Applied to {', '.join(done.files)} and committed as {sha[:12]} with its "
            f"receipt in git notes (see it with `git log --notes=qed`; share it with "
            f"`git push origin refs/notes/qed`). Git hooks did not run for this commit.{note}",
        )
    return ApplyOutcome(
        status="applied",
        task_id=wanted,
        grade=receipt.grade,
        files=done.files,
        message=f"Applied to {', '.join(done.files)}. Undo any time with `qed undo`.{note}",
    )


def commit_message(receipt, receipt_file: Path) -> str:
    """`<task>` + the summary line + QED-Grade and QED-Receipt trailers."""
    import hashlib

    from qedcode.verify.receipt import summary_line

    title = (receipt.task.request.strip().splitlines() or ["QEDCode fix"])[0][:72]
    digest = hashlib.sha256(receipt_file.read_bytes()).hexdigest()
    return (
        f"{title}\n\n{summary_line(receipt)}\n\n"
        f"QED-Grade: {receipt.grade.value}\nQED-Receipt: sha256:{digest}\n"
    )


def undo(root: Path | str | None = None) -> ApplyOutcome:
    """Take the last applied change back out of the user's files."""
    folder = resolve_root(root)
    try:
        files = undo_last(folder)
    except PatchRefused as exc:
        return ApplyOutcome(status="refused", files=exc.files, message=str(exc))
    except GitError as exc:
        return ApplyOutcome(status="error", message=str(exc))
    return ApplyOutcome(
        status="undone", files=files, message=f"Undone: {', '.join(files)} are back as they were."
    )


def status(root: Path | str | None = None) -> dict:
    """Is a job running in this project, and what was the last result?"""
    folder = resolve_root(root)
    holder = lock_holder(folder)
    return {
        "busy": holder is not None,
        "job": holder,
        "last": read_last_result(folder),
    }


def memory_list(root: Path | str | None = None) -> list[Lesson]:
    """The fixes QEDCode remembers for this project (newest first)."""
    return Memory(resolve_root(root)).lessons()


def memory_forget(lesson_id: int, root: Path | str | None = None) -> bool:
    """Forget one remembered fix. False when there is no such number."""
    return Memory(resolve_root(root)).forget(lesson_id)


def memory_clear(root: Path | str | None = None) -> int:
    """Forget everything for this project; returns how many fixes were forgotten."""
    return Memory(resolve_root(root)).clear()


def sandbox_status(root: Path | str | None = None):
    """Is the sealed test box (Docker, network off) on, and is it ready to use?"""
    from qedcode.engine import sandbox_setup

    return sandbox_setup.status(resolve_root(root))


def sandbox_setup(
    root: Path | str | None = None,
    confirm=None,
    on_progress=None,
):
    """Build the sealed test box image (asks first, because it downloads) and turn it on."""
    from qedcode.engine import sandbox_setup as setup_module

    return setup_module.setup(resolve_root(root), confirm or (lambda _prompt: False), on_progress)


def sandbox_off(root: Path | str | None = None) -> bool:
    """Run tests on this computer again. False when the sealed test box was already off."""
    from qedcode.engine import sandbox_setup as setup_module

    return setup_module.turn_off(resolve_root(root))


def build_tests(
    root: Path | str | None = None,
    path: str = "",
    *,
    speed: Speed | None = None,
    cb: Callbacks | None = None,
    config: GlobalConfig | None = None,
    client: ModelClient | None = None,
) -> SolveResult:
    """Safety Net Builder: tests for code no test covers, kept only if they catch mutants."""
    from qedcode.engine.safety_net import build_safety_net

    cfg = config or load_global()
    chosen = speed or cfg.engine.speed
    start = Path(root) if root is not None else Path.cwd()
    if not is_git_repo(start) or not has_commits(repo_root(start)):
        return _error_result(
            "qed tests", chosen, "Run this inside a git repository with at least one commit."
        )
    try:
        model = client or make_client(cfg, "main")
    except ModelError as exc:
        return _error_result("qed tests", chosen, str(exc))
    return build_safety_net(repo_root(start), path, chosen, cb or NonInteractiveCallbacks(), model)


def hunt(
    root: Path | str | None = None,
    path: str = "",
    *,
    speed: Speed | None = None,
    cb: Callbacks | None = None,
    config: GlobalConfig | None = None,
    client: ModelClient | None = None,
):
    """Bug Hunter: suspected bugs confirmed by a failing test, each with a proven fix if found."""
    from qedcode.engine.hunter import HuntResult
    from qedcode.engine.hunter import hunt as _hunt

    cfg = config or load_global()
    chosen = speed or cfg.engine.speed
    start = Path(root) if root is not None else Path.cwd()
    if not is_git_repo(start) or not has_commits(repo_root(start)):
        return HuntResult("error", "Run this inside a git repository with at least one commit.")
    try:
        model = client or make_client(cfg, "main")
    except ModelError as exc:
        return HuntResult("error", str(exc))
    callbacks = cb or NonInteractiveCallbacks()
    folder = repo_root(start)

    def solve_one(request: str, repro: tuple[str, str]) -> SolveResult:
        return solve(
            folder, request, speed=chosen, cb=callbacks, config=cfg, client=model, given_repro=repro
        )

    return _hunt(folder, path, chosen, callbacks, model, solve_one)


def night_failing_tests(root: Path | str | None = None) -> list[str]:
    """The tests failing on HEAD right now (for `qed night add --failing`)."""
    from qedcode.engine.check import project_config_for
    from qedcode.vcs.git import git_out
    from qedcode.verify.baseline import baseline_run

    folder = resolve_root(root)
    project = project_config_for(folder, [])
    head = git_out(folder, "rev-parse", "HEAD")
    run = baseline_run(folder, head, project, full=True, related=[], name="night-failing")
    return sorted(k for k, v in run.baseline.results.items() if v in ("failed", "error"))


def night_run(
    root: Path | str | None = None,
    *,
    cb: Callbacks | None = None,
    config: GlobalConfig | None = None,
    client: ModelClient | None = None,
    stop=None,
    on_task=None,
):
    """Solve every queued Night Shift task with quiet mode on and write the morning report."""
    from qedcode.engine.night import run_night

    cfg = (config or load_global()).model_copy(deep=True)
    cfg.engine.quiet = "on"  # the night shift is gentle by design
    folder = resolve_root(root)
    callbacks = cb or NonInteractiveCallbacks()
    model = client or make_client(cfg, "main")

    def solve_one(request: str, speed: Speed) -> SolveResult:
        return solve(folder, request, speed=speed, cb=callbacks, config=cfg, client=model)

    try:
        return run_night(folder, solve_one, stop=stop or (lambda: False), on_task=on_task)
    finally:
        try:
            model.unload()
        except Exception:  # noqa: BLE001 - unloading is best effort
            pass
