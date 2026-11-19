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
from qedcode.vcs import worktree
from qedcode.vcs.git import git
from qedcode.verify.pytest_runner import failed_before, run_pytest
from qedcode.verify.receipt import load_receipt

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
    """Re-check a receipt inside the repository `root`. Never raises for a bad receipt."""
    root = Path(root)
    try:
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
    cfg = project or load_project(root)
    python = cfg.python or sys.executable
    timeout = cfg.test_timeout_seconds
    _check_before(root, receipt, python, timeout, mismatch, cancelled)
    _check_after(root, receipt, _pairs(receipt), python, timeout, mismatch, cancelled)
    if not receipt.commands:
        result.notes.append(
            "This receipt has no test commands to re-run; only the patch was checked."
        )
    return result


def _run_command(
    wt: Path,
    command: list[str],
    python: str,
    timeout: float,
    cancelled: Callable[[], bool] | None,
):
    return run_pytest(
        wt,
        python,
        _selection_from(command),
        timeout,
        stage="targeted",
        command_prefix=_prefix_from(command, python),
        cancelled=cancelled,
    )


def _check_before(
    root: Path,
    receipt: Receipt,
    python: str,
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
        error = _apply(root, wt, receipt.patch, only=receipt.tests_added)
        if error:
            mismatch(f"The new test files from the patch could not be added to the base: {error}")
            return
        out = _run_command(wt, fbpa_command, python, timeout, cancelled)
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
    python: str,
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
            out = _run_command(wt, command, python, timeout, cancelled)
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
