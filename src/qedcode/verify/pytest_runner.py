"""Run pytest the same way every time and read its results reliably (AGENTS.md section 8).

    <python> -m pytest -q -p no:cacheprovider -o junit_family=xunit2 \
    --junitxml <run dir>/report.xml <selection>

The JUnit XML is the source of truth (test key = "<classname>::<name>"). The worktree's own code is
put first on PYTHONPATH so a project installed in editable mode into the user's environment can
never shadow the candidate being checked.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from qedcode.models.schemas import CheckResult, Stage, TestResult
from qedcode.verify.run import RunResult, run

DETAILS_LIMIT = 4000
SUMMARY_LIMIT = 300
TRACEBACK_LINES = 40
MAX_FAILURES_IN_DETAILS = 3

Status = Literal["ok", "failed", "no_tests", "collection_error", "timeout", "cancelled", "error"]


@dataclass
class PytestOutcome:
    """Raw pytest results for one run, plus a CheckResult for the cascade to build on."""

    status: Status
    results: list[TestResult] = field(default_factory=list)
    check: CheckResult = field(default_factory=lambda: CheckResult(stage="targeted", passed=False))
    exit_code: int | None = None
    duration_s: float = 0.0

    @property
    def passed_keys(self) -> list[str]:
        return [r.key for r in self.results if r.outcome == "passed"]

    @property
    def failing(self) -> list[TestResult]:
        return [r for r in self.results if r.outcome in ("failed", "error")]

    def as_map(self) -> dict[str, str]:
        return {r.key: r.outcome for r in self.results}


def pythonpath_for(worktree: Path, existing: str | None = None) -> str:
    """<worktree>/src and <worktree> first, then whatever PYTHONPATH already held."""
    parts = []
    if (worktree / "src").is_dir():
        parts.append(str(worktree / "src"))
    parts.append(str(worktree))
    if existing:
        parts.append(existing)
    return os.pathsep.join(parts)


def _trim_lines(text: str, limit: int = TRACEBACK_LINES) -> str:
    lines = text.strip().splitlines()
    if len(lines) <= limit:
        return "\n".join(lines)
    head = limit // 2
    return "\n".join([*lines[:head], "...", *lines[-(limit - head) :]])


def _first_line(text: str) -> str:
    for line in text.strip().splitlines():
        if line.strip():
            return line.strip()[:200]
    return ""


def parse_junit(xml_text: str) -> tuple[list[TestResult], str]:
    """Parse JUnit XML into test results and a trimmed failure report for the repairer."""
    root = ET.fromstring(xml_text)
    results: list[TestResult] = []
    reports: list[str] = []
    for case in root.iter("testcase"):
        classname = case.get("classname", "")
        name = case.get("name", "")
        key = f"{classname}::{name}" if classname else name
        outcome: Literal["passed", "failed", "error", "skipped"] = "passed"
        message = ""
        body = ""
        for child in case:
            if child.tag in ("failure", "error", "skipped"):
                outcome = "failed" if child.tag == "failure" else child.tag  # type: ignore[assignment]
                message = _first_line(child.get("message", "") or child.text or "")
                body = child.text or child.get("message", "") or ""
                break
        results.append(TestResult(key=key, outcome=outcome, message=message))
        if outcome in ("failed", "error") and len(reports) < MAX_FAILURES_IN_DETAILS:
            reports.append(f"{outcome.upper()} {key}\n{_trim_lines(body)}")
    return results, "\n\n".join(reports)


def _tail(text: str, lines: int = 12) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def _check(
    stage: Stage,
    passed: bool,
    summary: str,
    run_result: RunResult,
    results: list[TestResult],
    details: str,
    log_path: str | None,
) -> CheckResult:
    return CheckResult(
        stage=stage,
        passed=passed,
        command=run_result.args,
        exit_code=run_result.exit_code,
        duration_s=round(run_result.duration_s, 2),
        summary=summary[:SUMMARY_LIMIT],
        details=details[:DETAILS_LIMIT],
        tests=results,
        log_path=log_path,
    )


def run_pytest(
    worktree: Path | str,
    python: str,
    selection: Sequence[str] | None = None,
    timeout: float = 300.0,
    *,
    stage: Stage = "targeted",
    run_dir: Path | None = None,
    command_prefix: Sequence[str] | None = None,
    cancelled: Callable[[], bool] | None = None,
    env: dict[str, str] | None = None,
) -> PytestOutcome:
    """Run pytest in `worktree`. `selection` is a list of files/node ids; None or [] runs it all."""
    worktree = Path(worktree)
    temp_dir: Path | None = None
    if run_dir is None:
        temp_dir = Path(tempfile.mkdtemp(prefix="qed-pytest-"))
        run_dir = temp_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    report = run_dir / f"report-{stage}.xml"
    report.unlink(missing_ok=True)
    log_path = run_dir / f"{stage}.log"
    prefix = list(command_prefix) if command_prefix else [python, "-m", "pytest"]
    args = [
        *prefix,
        "-q",
        "-p",
        "no:cacheprovider",
        "-o",
        "junit_family=xunit2",
        "--junitxml",
        str(report),
        *(selection or []),
    ]
    run_env = {
        "PYTHONPATH": pythonpath_for(worktree, os.environ.get("PYTHONPATH")),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_ADDOPTS": "",
        **(env or {}),
    }
    result = run(
        args, cwd=worktree, timeout=timeout, env=run_env, log_path=log_path, cancelled=cancelled
    )
    try:
        return _interpret(
            result, report, python, stage, timeout, str(log_path) if temp_dir is None else None
        )
    finally:
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)


def _interpret(
    result: RunResult,
    report: Path,
    python: str,
    stage: Stage,
    timeout: float,
    log_path: str | None,
) -> PytestOutcome:
    def outcome(
        status: Status, passed: bool, summary: str, results=None, details=""
    ) -> PytestOutcome:
        results = results or []
        check = _check(stage, passed, summary, result, results, details, log_path)
        return PytestOutcome(status, results, check, result.exit_code, result.duration_s)

    if result.cancelled:
        return outcome("cancelled", False, "The test run was cancelled.")
    if result.timed_out:
        return outcome(
            "timeout",
            False,
            f"Tests did not finish within {int(timeout)} seconds and were stopped.",
            details="A test may be waiting for the network or for input. "
            "Lower test_timeout_seconds in .qed/project.toml or fix the slow test.",
        )
    if result.error:
        return outcome("error", False, f"pytest could not start: {result.error}"[:SUMMARY_LIMIT])
    text = ""
    try:
        text = report.read_text(encoding="utf-8")
    except OSError:
        pass
    if not text:
        combined = result.output
        if "No module named pytest" in combined or "No module named 'pytest'" in combined:
            return outcome(
                "error", False, f"pytest is not installed in {python}.", details=_tail(combined)
            )
        if result.exit_code == 5:
            return outcome("no_tests", True, "No tests were collected.", details=_tail(combined))
        return outcome(
            "error",
            False,
            f"pytest stopped without a report (exit {result.exit_code}).",
            details=_tail(combined),
        )
    try:
        results, details = parse_junit(text)
    except ET.ParseError:
        return outcome("error", False, "pytest wrote a report that could not be read.")
    failed = [r for r in results if r.outcome in ("failed", "error")]
    errors = [r for r in results if r.outcome == "error"]
    passed_count = sum(1 for r in results if r.outcome == "passed")
    if not results:
        status: Status = "no_tests" if result.exit_code in (0, 5) else "error"
        summary = (
            "No tests were collected."
            if status == "no_tests"
            else (f"pytest ran but reported no tests (exit {result.exit_code}).")
        )
        return outcome(status, status == "no_tests", summary, details=_tail(result.output))
    if errors and result.exit_code == 2:
        return outcome(
            "collection_error",
            False,
            f"{len(errors)} test file(s) could not be collected: {errors[0].message}"[
                :SUMMARY_LIMIT
            ],
            results,
            details,
        )
    if failed:
        return outcome(
            "failed",
            False,
            f"{len(failed)} of {len(results)} tests failed; first: {failed[0].key}"[:SUMMARY_LIMIT],
            results,
            details,
        )
    return outcome("ok", True, f"{passed_count}/{len(results)} tests pass.", results)
