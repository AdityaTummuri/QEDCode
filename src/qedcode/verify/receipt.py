"""Proof receipts: JSON that lists every check that ran, so `qed verify` can re-run it.

Receipts never contain API keys, prompts or code beyond the patch itself (AGENTS.md rule 8).
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import time
from pathlib import Path

from qedcode import __version__
from qedcode.config import ProjectConfig
from qedcode.models.schemas import Baseline, Candidate, CheckResult, Receipt, TaskSpec
from qedcode.project.paths import is_test_file
from qedcode.verify.guard import (
    caught_from_checks,
    expectations_from_checks,
    parse_diff,
    protected_from_checks,
    risk_findings,
    weakened_from_checks,
)
from qedcode.verify.test_additions import key_in_file

RECEIPTS_DIR = Path(".qed") / "receipts"
RECEIPT_VIEWER_URL = "https://adityatummuri.github.io/QEDCode/receipt/"
LINK_PREFIX = "#r="
MAX_LINK_BYTES = 64 * 1024
NOT_RUNNABLE_PREFIX = "pytest could not run before the change"
NO_CODE_CHANGE_PREFIX = "The change alters no code outside tests"
DISPUTED_GAP = (
    "Two generated tests disagreed about this task: the change passes the second and fails the "
    "first, so check that the expected behaviour is the one you want."
)
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
    if receipt.mutation is not None and receipt.mutation.total:
        parts.append(f"proof strength {receipt.mutation.killed}/{receipt.mutation.total}")
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
    if cand.disputed_repro:
        gaps.append(DISPUTED_GAP)
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
    caught = caught_from_checks(cand.checks)
    gaps.extend(f"Caught: {reason}." for reason in caught)
    moved = expectations_from_checks(cand.checks)
    if moved:
        gaps.append(
            "Tests expect something else than before (as they were, they fail with this change): "
            f"{', '.join(moved)}."
        )
    weakened = weakened_from_checks(cand.checks)
    if weakened and not caught:
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


def receipt_link(receipt: Receipt, viewer: str = RECEIPT_VIEWER_URL) -> str:
    """A link that opens `receipt` in the Receipt Viewer; the receipt rides in the fragment.

    Browsers never send the fragment to a server, so the link stores nothing anywhere. The same
    receipt always gives the same link (gzip with mtime 0). Raises ValueError when too long."""
    raw = receipt.model_dump_json().encode("utf-8", "surrogateescape")
    data = base64.urlsafe_b64encode(gzip.compress(raw, mtime=0)).rstrip(b"=").decode("ascii")
    link = f"{viewer.split('#', 1)[0]}{LINK_PREFIX}{data}"
    if len(link) > MAX_LINK_BYTES:
        raise ValueError(
            f"This receipt is too big for a link ({len(link) // 1024} KB; the limit is "
            f"{MAX_LINK_BYTES // 1024} KB, usually because the patch is large). Share the "
            "receipt file instead: anyone can open it in the Receipt Viewer or run `qed verify`."
        )
    return link


def is_receipt_link(text: str) -> bool:
    return LINK_PREFIX in str(text) and str(text).startswith(("http://", "https://"))


def receipt_from_link(link: str) -> Receipt:
    """Decode a receipt link back into a Receipt; ValueError with a plain message otherwise."""
    _, _, data = str(link).partition(LINK_PREFIX)
    data = data.split("&", 1)[0].strip()
    if not data:
        raise ValueError("This link has no receipt in it (it should contain '#r=').")
    try:
        raw = gzip.decompress(base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)))
    except (ValueError, OSError, EOFError) as exc:
        raise ValueError(
            "The receipt link is damaged (it could not be decoded). Copy the whole link, "
            "including everything after '#r='."
        ) from exc
    try:
        return Receipt.model_validate_json(raw)
    except ValueError as exc:
        raise ValueError(
            f"The link does not hold a valid QEDCode receipt ({str(exc)[:120]})."
        ) from exc


def describe(receipt: Receipt) -> list[str]:
    """The receipt in plain English, one line per item, for `qed receipt`."""
    lines = [summary_line(receipt), ""]
    lines.append(f"Task: {receipt.task.request}")
    lines.append(f"Project: {receipt.project_name} · base commit {receipt.base_commit[:12]}")
    lines.append(f"Made by QEDCode {receipt.qed_version} on {receipt.created_at}")
    model = receipt.model_info.get("model")
    if model:
        temps = ", ".join(str(x) for x in receipt.model_info.get("temperatures") or [])
        lines.append(f"Model: {model}" + (f" (temperatures {temps})" if temps else ""))
    lines += ["", "Checks that ran:"]
    for check in receipt.checks:
        lines.append(f"  {check.stage}: {'ok' if check.passed else 'FAILED'} - {check.summary}")
    if receipt.fbpa_tests:
        lines += ["", "Tests that failed before the change and pass after it:"]
        lines += [f"  {key}" for key in receipt.fbpa_tests]
    mutation = receipt.mutation
    if mutation is not None and mutation.total:
        lines += [
            "",
            f"Proof strength: the new tests caught {mutation.killed} of {mutation.total} small "
            "breakages (mutants) of the changed lines",
        ]
        lines += [f"  survived: {s}" for s in mutation.survivors]
    lines += ["", "Not proven:"]
    lines += [f"  - {gap}" for gap in receipt.unverified] or ["  (nothing listed)"]
    lines += ["", f"Patch SHA-256: {receipt.patch_sha256}"]
    return lines


def explanation(receipt: Receipt, plan: list[str] = ()) -> str:  # type: ignore[assignment]
    """What changed and what shows it works, in a few sentences, from the receipt alone."""
    files = [f for f in parse_diff(receipt.patch) if f.path not in receipt.tests_added]
    where = ", ".join(f.path for f in files[:3]) or "your project"
    added = sum(len(f.added) for f in files)
    removed = sum(len(f.removed) for f in files)
    parts = [f"Changed {where} ({added} line(s) added, {removed} removed)."]
    idea = next((line.strip(" -") for line in plan if line.strip(" -")), "")
    if idea:
        parts.append(f"The model's plan: {idea.rstrip('.')}.")
    passed, total = test_counts(receipt.checks)
    if receipt.fbpa_tests:
        names = ", ".join(k.rsplit("::", 1)[-1] for k in receipt.fbpa_tests[:2])
        more = f" and {len(receipt.fbpa_tests) - 2} more" if len(receipt.fbpa_tests) > 2 else ""
        parts.append(f"Failed before the change, passing with it: {names}{more}.")
    if total:
        parts.append(f"{passed} of {total} tests pass.")
    if DISPUTED_GAP in receipt.unverified:
        parts.append(DISPUTED_GAP)
    return " ".join(parts)


def next_step(receipt: Receipt) -> str:
    """The one thing that would raise this receipt's grade, in plain English ("" for grade A).

    It mirrors the rules in verify/grade.py, read back from the receipt, so the CLI, the MCP
    server and the GitHub Action all give the same advice."""
    if receipt.grade.value == "A":
        return ""
    gaps = receipt.unverified
    weakened = weakened_from_checks(receipt.checks)
    if any(g.startswith(NOT_RUNNABLE_PREFIX) for g in gaps):
        return "Make the tests runnable (the receipt says why they could not); see `qed doctor`."
    caught = caught_from_checks(receipt.checks)
    if caught:
        return (
            f"Put the tests back as they were and fix the code instead ({caught[0]}). "
            "Then add a new test that fails before your fix."
        )
    if weakened:  # first: a weakened test is the thing to undo, whatever else changed
        return (
            f"Undo the change that weakens tests in {', '.join(weakened)} (a skip, removed asserts "
            "or an assert that can never fail); passing tests prove nothing while it is there."
        )
    if any(g.startswith(NO_CODE_CHANGE_PREFIX) for g in gaps):
        return (
            "Nothing to prove: only tests, comments, formatting or files no test uses changed. "
            "Change the code itself to fix something."
        )
    passed, _total = test_counts(receipt.checks)
    if not passed:
        return "Add a test that covers the change: no existing test exercised it."
    if receipt.protected_changes:
        return (
            f"Leave existing tests as they were ({', '.join(receipt.protected_changes)} changed); "
            "add new test functions instead."
        )
    if any(g.startswith("You approved a risky pattern") for g in gaps):
        return (
            "Avoid the risky pattern you approved (see the receipt); with it the best grade is B."
        )
    if DISPUTED_GAP in gaps:
        return "Read the new test: two generated tests disagreed about what is right."
    if not receipt.fbpa_tests:
        return (
            f"Add a test that fails on {receipt.base_ref} and passes with this change, so the fix "
            "is proven, not only tested."
        )
    if any(g.startswith("Full suite not run:") for g in gaps):
        return "The full suite is too slow to run here, so B is the best grade for this project."
    if any(g.startswith("Full suite not run") for g in gaps):
        return "Run the full suite too: use --speed normal or sure."
    return ""
