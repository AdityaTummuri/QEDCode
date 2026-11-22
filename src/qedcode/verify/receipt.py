"""Proof receipts: JSON that lists every check that ran, so `qed verify` can re-run it.

Receipts never contain API keys, prompts or code beyond the patch itself (AGENTS.md rule 8).
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from qedcode import __version__
from qedcode.config import ProjectConfig
from qedcode.models.schemas import Baseline, Candidate, CheckResult, Receipt, TaskSpec
from qedcode.project.paths import is_test_file
from qedcode.verify.guard import (
    parse_diff,
    protected_from_checks,
    risk_findings,
    weakened_from_checks,
)
from qedcode.verify.test_additions import key_in_file

RECEIPTS_DIR = Path(".qed") / "receipts"
NOT_RUNNABLE_PREFIX = "pytest could not run before the change"
NO_CODE_CHANGE_PREFIX = "The change alters no code outside tests"
NETWORK_NOTE = "Tests ran on this computer without network blocking (v1 limitation)."
SEALED_NOTE = (
    "Tests ran in a sealed Docker container with the network off; the project's dependencies "
    "are the ones installed when the box was built (`qed sandbox setup`)."
)


def receipt_path(root: Path | str, task_id: str) -> Path:
    return Path(root) / RECEIPTS_DIR / f"{task_id}.json"


def templatize_command(argv: list[str], python: str) -> list[str]:
    """Replace the interpreter with {python} and the report path with {report}."""
    out: list[str] = []
    for index, arg in enumerate(argv):
        if index > 0 and argv[index - 1] == "--junitxml":
            out.append("{report}")
        else:
            out.append("{python}" if arg == python else arg)
    return out


def _last_test_check(checks: list[CheckResult]) -> CheckResult | None:
    for stage in ("full", "targeted"):
        for check in reversed(checks):
            if check.stage == stage and check.tests:
                return check
    return None


def test_counts(checks: list[CheckResult]) -> tuple[int, int]:
    """(passed, total) from the most complete test run recorded; skipped tests are not counted."""
    check = _last_test_check(checks)
    if check is None:
        return 0, 0
    passed = sum(1 for t in check.tests if t.outcome == "passed")
    total = sum(1 for t in check.tests if t.outcome in ("passed", "failed", "error"))
    return passed, total


def summary_line(receipt: Receipt) -> str:
    """`Grade A · repro test failed before, passes now · 48/48 tests pass · lint clean · ...`"""
    parts = [f"Grade {receipt.grade.value}"]
    if receipt.fbpa_tests:
        parts.append("repro test failed before, passes now")
    passed, total = test_counts(receipt.checks)
    if total:
        parts.append(f"{passed}/{total} tests pass")
    else:
        blocked = next((g for g in receipt.unverified if g.startswith(NOT_RUNNABLE_PREFIX)), "")
        reason = blocked[len(NOT_RUNNABLE_PREFIX) :].strip(" .()")
        parts.append(f"tests could not run: {reason}" if reason else "no tests ran")
    lint_ok = any(c.stage == "lint" and c.passed for c in receipt.checks)
    parts.append("lint clean" if lint_ok else "lint not run")
    parts.append(
        "no protected files changed" if not receipt.protected_changes else "protected files changed"
    )
    weakened = weakened_from_checks(receipt.checks)
    if weakened:
        parts.append(f"tests weakened in {', '.join(weakened)}")
    if any(g.startswith(NO_CODE_CHANGE_PREFIX) for g in receipt.unverified):
        parts.append("no code changed outside tests")
    return " · ".join(parts)


def unverified_gaps(
    cand: Candidate,
    baseline: Baseline,
    task: TaskSpec,
    fbpa_tests: list[str],
    model_info: dict,
    sandboxed: bool = False,
) -> list[str]:
    """Every gap in the proof, in plain English."""
    gaps: list[str] = []
    if not baseline.runnable:
        gaps.append(f"{NOT_RUNNABLE_PREFIX} ({baseline.reason or 'unknown reason'}).")
    if not cand.full_suite_ran:
        if baseline.full_skipped:
            gaps.append(f"Full suite not run: {baseline.full_skipped}, too slow for every task.")
        else:
            why = " (fast speed)" if task.speed.value == "fast" else ""
            gaps.append(f"Full suite not run{why}.")
    if baseline.slow_tests:
        shown = ", ".join(baseline.slow_tests[:5]) + (
            ", ..." if len(baseline.slow_tests) > 5 else ""
        )
        gaps.append(f"Related test files left out because each is too slow to run: {shown}")
    known = sorted(k for k, v in baseline.results.items() if v in ("failed", "error"))
    if known:
        shown = ", ".join(known[:5]) + (", ..." if len(known) > 5 else "")
        gaps.append(f"{len(known)} test(s) were already failing before this change: {shown}")
    if not cand.code_changed:
        gaps.append(
            f"{NO_CODE_CHANGE_PREFIX} (only comments, formatting, or files the tests never use "
            "such as docs or .gitignore), so passing tests say nothing about it."
        )
    if cand.tests_passed == 0:
        gaps.append("No existing test covered this change.")
    if not fbpa_tests:
        gaps.append("No test was shown to fail before the change and pass after it.")
    risk = next((c for c in cand.checks if c.stage == "risk"), None)
    if risk is not None and risk.needs_approval and risk.passed:
        seen = []
        for finding in risk_findings(cand.diff):
            text = f"You approved a risky pattern: {finding.describe()}."
            if text not in seen:
                seen.append(text)
        gaps.extend(seen)
    elif risk is not None and risk.summary.startswith("Warning:"):
        gaps.append(f"Your change contains risky code ({risk.summary.removeprefix('Warning: ')}).")
    protected = protected_from_checks(cand.checks)
    if protected:
        gaps.append(f"Protected files were changed: {', '.join(protected)}.")
    weakened = weakened_from_checks(cand.checks)
    if weakened:
        gaps.append(
            f"Tests were weakened in {', '.join(weakened)} (a skip, removed asserts or an assert "
            "that can never fail), so passing tests are no evidence."
        )
    if model_info.get("helper_used"):
        gaps.append(
            f"Parts of your code were sent to the helper model ({model_info.get('helper', '')})."
        )
    elif model_info.get("backend") == "cloud":
        gaps.append(
            f"Parts of your code were sent to a cloud model ({model_info.get('model', '')})."
        )
    gaps.append(SEALED_NOTE if sandboxed else NETWORK_NOTE)
    return gaps


def _tests_added(diff: str, fbpa_tests: list[str]) -> list[str]:
    """Test files `qed verify` adds to the base: new test files, and existing test files that
    gained a fail-before-pass-after test (tests appended in `qed check`)."""
    added = set()
    for f in parse_diff(diff):
        if not is_test_file(f.path) or f.status == "deleted":
            continue
        if f.status == "added" or any(key_in_file(k, f.path) for k in fbpa_tests):
            added.add(f.path)
    return sorted(added)


def build_receipt(
    *,
    root: Path | str,
    task: TaskSpec,
    cand: Candidate,
    baseline: Baseline,
    project: ProjectConfig,
    base_commit: str,
    base_ref: str,
    model_info: dict | None = None,
) -> Receipt:
    """Assemble the receipt for a graded candidate. Raises ValueError for a rejected one."""
    if cand.grade is None:
        raise ValueError("A rejected candidate has no grade, so it gets no receipt.")
    model_info = dict(model_info or {})
    fbpa_tests = [t.key for c in cand.checks if c.stage == "fbpa" for t in c.tests]
    python = project.python
    commands = [
        templatize_command(list(c.command), python) for c in cand.checks if c.command is not None
    ]
    return Receipt(
        qed_version=__version__,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        project_name=Path(root).name,
        task=task,
        base_commit=base_commit,
        base_ref=base_ref,
        patch=cand.diff,
        patch_sha256=hashlib.sha256(cand.diff.encode("utf-8", "surrogateescape")).hexdigest(),
        tests_added=_tests_added(cand.diff, fbpa_tests),
        fbpa_tests=fbpa_tests,
        known_failures=sorted(k for k, v in baseline.results.items() if v in ("failed", "error")),
        checks=cand.checks,
        protected_changes=protected_from_checks(cand.checks),
        grade=cand.grade,
        unverified=unverified_gaps(
            cand, baseline, task, fbpa_tests, model_info, sandboxed=project.sandbox == "docker"
        ),
        model_info=model_info,
        commands=commands,
        sandbox="docker" if project.sandbox == "docker" else "",
    )


def save_receipt(root: Path | str, receipt: Receipt) -> Path:
    """Write `.qed/receipts/<task id>.json` and return its path."""
    path = receipt_path(root, receipt.task.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(receipt.model_dump_json(indent=1), encoding="utf-8")
    return path


def load_receipt(path: Path | str) -> Receipt:
    """Read a receipt file; raises ValueError with a plain message when it is not a receipt."""
    try:
        return Receipt.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(
            f"Cannot read the receipt {path}: {exc}. Check the path (receipts are saved in "
            ".qed/receipts/)."
        ) from exc
    except ValueError as exc:
        raise ValueError(
            f"{path} is not a valid QEDCode receipt ({str(exc)[:120]}). "
            "Pass a file from .qed/receipts/."
        ) from exc
