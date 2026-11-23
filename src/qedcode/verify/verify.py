"""`qed verify`: re-run a receipt's checks and compare (AGENTS.md section 6).

Steps: check the patch checksum, find the base commit in this repository, apply the patch in a
fresh worktree, re-run the receipt's commands, and compare every check's pass flag and every
fail-before-pass-after test (it must fail on the base and pass with the patch).
"""

from __future__ import annotations

import hashlib
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from qedcode.config import ProjectConfig, load_project
from qedcode.models.schemas import CheckResult, Receipt
from qedcode.project.detect import detect_project
from qedcode.project.detect_js import detect_js, js_config
from qedcode.project.paths import validate_path
from qedcode.vcs import worktree
from qedcode.vcs.git import git
from qedcode.verify.js_runner import is_js_command, selection_from
from qedcode.verify.pytest_runner import failed_before
from qedcode.verify.receipt import is_receipt_link, load_receipt, receipt_from_link
from qedcode.verify.runners import run_tests
from qedcode.verify.sandbox import sandbox_for

PYTHON = "{python}"


@dataclass
class VerifyResult:
    verified: bool
    mismatches: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    receipt: Receipt | None = None


def _sha(patch: str) -> str:
    return hashlib.sha256(patch.encode("utf-8", "surrogateescape")).hexdigest()


def _commit_exists(root: Path, sha: str) -> bool:
    return git(root, "cat-file", "-e", f"{sha}^{{commit}}").ok


def _pairs(receipt: Receipt) -> list[tuple[CheckResult, list[str]]]:
    """Each recorded check that ran a command, with that (templated) command."""
    with_command = [c for c in receipt.checks if c.command is not None]
    return list(zip(with_command, receipt.commands, strict=False))


def _selection_from(command: list[str]) -> list[str]:
    """The test selection at the end of a templated pytest command (after --junitxml {report})."""
    if "--junitxml" in command:
        index = command.index("--junitxml") + 2
        return command[index:]
    return []


def _prefix_from(command: list[str], python: str) -> list[str]:
    """Everything before the standard flags, with {python} filled in."""
    end = command.index("-q") if "-q" in command else len(command)
    return [python if part == PYTHON else part for part in command[:end]]


def _templated_prefix(command: list[str], cfg: ProjectConfig) -> list[str]:
    """The command before the standard flags, with this project's interpreter as {python}."""
    end = command.index("-q") if "-q" in command else len(command)
    own = {cfg.python, sys.executable} - {""}
    return [PYTHON if part in own else part for part in command[:end]]


def unsafe_command(command: list[str], cfg: ProjectConfig) -> str | None:
    """Why a recorded command must not run here, or None when it is safe to re-run.

    A receipt is a file anyone can hand over, so it is data, never a command list: only this
    project's own test command (or the default `{python} -m pytest`) may run, and the test
    selection may name only files or test ids inside the project, never options.
    """
    selection = selection_from(command) if is_js_command(command) else _selection_from(command)
    if not is_js_command(command):
        prefix = _templated_prefix(command, cfg)
        allowed = (list(cfg.test_command), [PYTHON, "-m", "pytest"])
        if prefix not in allowed:
            return (
                f"it asks to run `{' '.join(prefix) or '(nothing)'}`, which is not this project's "
                "test command (see test_command in .qed/project.toml)"
            )
    for item in selection:
        path = item.split("::", 1)[0]
        problem = validate_path(path) if path else "an empty test name"
        if problem:
            return f"its test selection has '{item[:80]}', which is not a test in this project"
    return None


def _apply(root: Path, wt: Path, patch: str, only: list[str] | None = None) -> str:
    args = ["apply", "--binary", "--whitespace=nowarn"]
    for path in only or []:
        args += ["--include", path]
    result = git(wt, *args, stdin_text=patch, exact=True)
    return "" if result.ok else (result.output.splitlines() or ["git apply failed"])[-1]


def verify_receipt(
    receipt_path: Path | str,
    root: Path | str,
    *,
    project: ProjectConfig | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> VerifyResult:
    """Re-check a receipt (a file, or a Receipt Viewer link) inside the repository `root`.

    Never raises for a bad receipt."""
    root = Path(root)
    try:
        if is_receipt_link(str(receipt_path)):
            receipt = receipt_from_link(str(receipt_path))
        else:
            receipt = load_receipt(receipt_path)
    except ValueError as exc:
        return VerifyResult(False, [str(exc)])
    result = VerifyResult(True, receipt=receipt)

    def mismatch(message: str) -> None:
        result.verified = False
        result.mismatches.append(message)

    if _sha(receipt.patch) != receipt.patch_sha256:
        mismatch(
            "The patch inside the receipt was changed after it was written "
            "(its checksum does not match)."
        )
        return result
    if not _commit_exists(root, receipt.base_commit):
        mismatch(
            f"The base commit {receipt.base_commit[:10]} is not in this repository, so the proof "
            "cannot be re-run here. Run `qed verify` inside the repository where the receipt was "
            "made (its snapshot must still exist)."
        )
        return result
    cfg = project or _project_config(root, result.notes)
    if any(is_js_command(c) for c in receipt.commands) and cfg.language != "javascript":
        cfg = js_config(detect_js(root))  # a JavaScript receipt in a folder not set up yet
    refused = [why for c in receipt.commands if (why := unsafe_command(c, cfg))]
    if refused:
        mismatch(
            f"Nothing was re-run, because this receipt is not safe to run here: {refused[0]}. "
            "Only re-run receipts that QEDCode wrote in this project."
        )
        return result
    timeout = cfg.test_timeout_seconds
    sandbox = sandbox_for(cfg, root)
    if receipt.sandbox == "docker" and sandbox is None:
        result.notes.append(
            "This receipt was made in a sealed test box (Docker), but the sealed test box is off "
            "here, so its tests were re-run on this computer. Run `qed sandbox setup` to match it."
        )
    _check_before(root, receipt, cfg, timeout, mismatch, cancelled)
    _check_after(root, receipt, _pairs(receipt), cfg, timeout, mismatch, cancelled)
    if not receipt.commands:
        result.notes.append(
            "This receipt has no test commands to re-run; only the patch was checked."
        )
    return result


def _project_config(root: Path, notes: list[str]) -> ProjectConfig:
    """The confirmed project config, else the detected one, the same choice `qed check` makes.

    Without this, a folder never opened in the terminal UI re-ran its receipts with QEDCode's own
    interpreter (no pytest when QEDCode is a `uv tool`), so a sound receipt was NOT VERIFIED."""
    saved = load_project(root)
    if saved.confirmed:
        return saved
    detected = detect_project(root)
    if detected.config.language == "python" and detected.config.python:
        notes.append(f"Re-ran the tests with the detected Python ({detected.config.python}).")
    return detected.config


def _run_command(
    wt: Path,
    root: Path,
    command: list[str],
    cfg: ProjectConfig,
    timeout: float,
    cancelled: Callable[[], bool] | None,
):
    """Re-run one recorded command: JavaScript commands by their runner, pytest as recorded."""
    if is_js_command(command):
        js = (
            cfg
            if cfg.language == "javascript"
            else cfg.model_copy(update={"language": "javascript"})
        )
        return run_tests(
            wt, js, root, selection_from(command), timeout, stage="targeted", cancelled=cancelled
        )
    py = cfg if cfg.language == "python" else cfg.model_copy(update={"language": "python"})
    python = py.python or sys.executable
    return run_tests(
        wt,
        py,
        root,
        _selection_from(command),
        timeout,
        stage="targeted",
        cancelled=cancelled,
        command_prefix=_prefix_from(command, python),
    )


def _check_before(
    root: Path,
    receipt: Receipt,
    cfg: ProjectConfig,
    timeout: float,
    mismatch: Callable[[str], None],
    cancelled: Callable[[], bool] | None,
) -> None:
    """Every fail-before-pass-after test must FAIL on the base (with only the new tests added)."""
    if not receipt.fbpa_tests:
        return
    fbpa_command = next((cmd for check, cmd in _pairs(receipt) if check.stage == "fbpa"), None)
    if fbpa_command is None:
        mismatch("The receipt lists a fail-before-pass-after test but not the command that ran it.")
        return
    wt = worktree.create(root, receipt.base_commit, f"verify-{receipt.task.id}-before")
    try:
        # No test file in the patch: the tests that failed before are the project's own, and
        # nothing is added to the base (an empty list must never mean "the whole patch").
        error = (
            _apply(root, wt, receipt.patch, only=receipt.tests_added) if receipt.tests_added else ""
        )
        if error:
            mismatch(f"The new test files from the patch could not be added to the base: {error}")
            return
        out = _run_command(wt, root, fbpa_command, cfg, timeout, cancelled)
        outcomes = out.as_map()
        for key in receipt.fbpa_tests:
            if not failed_before(key, outcomes):
                mismatch(
                    f"Test {key} was supposed to fail before the change but it "
                    f"{'passes' if outcomes.get(key) == 'passed' else 'did not run'} on the base."
                )
    finally:
        worktree.remove(root, wt)


def _check_after(
    root: Path,
    receipt: Receipt,
    pairs: list[tuple[CheckResult, list[str]]],
    cfg: ProjectConfig,
    timeout: float,
    mismatch: Callable[[str], None],
    cancelled: Callable[[], bool] | None,
) -> None:
    wt = worktree.create(root, receipt.base_commit, f"verify-{receipt.task.id}-after")
    try:
        error = _apply(root, wt, receipt.patch)
        if error:
            mismatch(f"The patch does not apply to the base commit any more: {error}")
            return
        known = set(receipt.known_failures)
        for check, command in pairs:
            out = _run_command(wt, root, command, cfg, timeout, cancelled)
            if out.status in ("timeout", "error", "cancelled"):
                mismatch(f"The {check.stage} check could not be re-run: {out.check.summary}")
                continue
            new_failures = [r.key for r in out.failing if r.key not in known]
            now_passed = not new_failures
            if now_passed != check.passed:
                mismatch(
                    f"The {check.stage} check was recorded as "
                    f"{'passed' if check.passed else 'failed'} but now "
                    f"{'passes' if now_passed else 'fails: ' + ', '.join(new_failures[:3])}."
                )
            if check.stage == "fbpa":
                outcomes = out.as_map()
                for key in receipt.fbpa_tests:
                    if outcomes.get(key) != "passed":
                        mismatch(f"Test {key} should pass with the change but it does not.")
    finally:
        worktree.remove(root, wt)
