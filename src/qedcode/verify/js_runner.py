"""Run JavaScript and TypeScript tests and read the results (AGENTS.md section 15.3).

Three runners, one result type (`PytestOutcome`, so the cascade, baseline and `qed verify` work
unchanged): Node's built-in `node --test` (with QEDCode's own reporter), Vitest and Jest.
Vitest and Jest start from `node_modules/.bin`, never through `npx`, so nothing is downloaded.
Test keys are `<file>::<full test name>`; a file that cannot load is the key `<file>` alone, which
the fail-before-pass-after rule treats like a pytest collection error.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from qedcode.config import ProjectConfig
from qedcode.models.schemas import Stage, TestResult
from qedcode.project.detect_js import find_js_test_files, runner_binary
from qedcode.verify.pytest_runner import (
    SUMMARY_LIMIT,
    PytestOutcome,
    Status,
    _check,
    _tail,
    _trim_lines,
    failure_report,
)
from qedcode.verify.run import RunResult, run

REPORTER = Path(__file__).with_name("node_reporter.mjs")
NAME_SEPARATOR = " > "
SEPARATOR = "--"  # recorded commands: everything after it is the test selection


@dataclass
class RawResult:
    file: str  # repo-relative, forward slashes
    name: str  # full test name (ancestors joined), empty for a whole-file failure
    status: str  # passed | failed | skipped
    message: str = ""
    body: str = ""


# --------------------------------------------------------------------------- commands


def recorded_command(project: ProjectConfig, selection: Sequence[str]) -> list[str]:
    """The command as receipts store it: placeholders instead of machine-specific paths."""
    runner = project.test_runner
    if runner == "node":
        head = ["{node}", "--test", "--test-reporter", "{reporter}",
                "--test-reporter-destination", "{report}"]  # fmt: skip
    elif runner == "vitest":
        head = ["{vitest}", "run", "--reporter=json", "--outputFile={report}"]
    else:
        head = ["{jest}", "--json", "--outputFile={report}", "--ci", "--runTestsByPath"]
    return [*head, SEPARATOR, *selection]


def selection_from(command: list[str]) -> list[str]:
    """The test selection at the end of a recorded JavaScript command (after `--`)."""
    return command[command.index(SEPARATOR) + 1 :] if SEPARATOR in command else []


def is_js_command(command: list[str]) -> bool:
    return bool(command) and command[0] in ("{node}", "{vitest}", "{jest}")


def real_command(
    project: ProjectConfig, node: str, binary: Path | None, report: Path, files: Sequence[str]
) -> list[str]:
    runner = project.test_runner
    if runner == "node":
        # a file:// URL: on Windows Node reads a bare "C:\\..." specifier as a URL scheme "c:"
        return [node, "--test", "--test-reporter", REPORTER.as_uri(),
                "--test-reporter-destination", str(report), *files]  # fmt: skip
    assert binary is not None
    if runner == "vitest":
        return [str(binary), "run", "--reporter=json", f"--outputFile={report}", *files]
    return [str(binary), "--json", f"--outputFile={report}", "--ci", "--runTestsByPath", *files]


# --------------------------------------------------------------------------- reading reports


def _relative(path: str, worktree: Path) -> str:
    try:
        return Path(os.path.realpath(path)).relative_to(os.path.realpath(worktree)).as_posix()
    except ValueError:
        return path.replace("\\", "/")


def parse_node_report(text: str, worktree: Path) -> list[RawResult]:
    """Results from QEDCode's node reporter (see node_reporter.mjs)."""
    data = json.loads(text)
    results: list[RawResult] = []
    for item in data.get("results", []):
        file = _relative(item.get("file") or "", worktree)
        whole = bool(item.get("wholeFile"))
        names = [*item.get("path", []), item.get("name", "")]
        status = {"passed": "passed", "failed": "failed"}.get(item.get("status", ""), "skipped")
        results.append(
            RawResult(
                file=file,
                name="" if whole else NAME_SEPARATOR.join(n for n in names if n),
                status=status,
                message=str(item.get("message", "")),
                body=str(item.get("stack", "") or item.get("message", "")),
            )
        )
    return results


def parse_jest_report(text: str, worktree: Path) -> list[RawResult]:
    """Results from `jest --json` or `vitest --reporter=json` (the same shape)."""
    data = json.loads(text)
    results: list[RawResult] = []
    for suite in data.get("testResults", []):
        file = _relative(str(suite.get("name", "")), worktree)
        asserted = suite.get("assertionResults") or []
        for item in asserted:
            state = str(item.get("status", ""))
            status = "passed" if state == "passed" else "failed" if state == "failed" else "skipped"
            messages = item.get("failureMessages") or []
            full = item.get("fullName") or NAME_SEPARATOR.join(
                [*item.get("ancestorTitles", []), item.get("title", "")]
            )
            results.append(
                RawResult(
                    file=file,
                    name=str(full).strip(),
                    status=status,
                    message=str(messages[0]).strip().splitlines()[0] if messages else "",
                    body="\n".join(str(m) for m in messages),
                )
            )
        if not asserted and suite.get("status") == "failed":
            message = str(suite.get("message", "")).strip()
            results.append(
                RawResult(file, "", "failed", message.splitlines()[0] if message else "", message)
            )
    return results


def to_outcome(raw: list[RawResult]) -> tuple[list[TestResult], dict[str, str]]:
    results: list[TestResult] = []
    bodies: dict[str, str] = {}
    seen: dict[str, int] = {}
    for item in raw:
        key = f"{item.file}::{item.name}" if item.name else item.file
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:  # the same test name twice in one file: keep both, distinguishable
            key = f"{key} #{seen[key]}"
        outcome = "passed" if item.status == "passed" else "skipped"
        if item.status == "failed":
            outcome = "error" if not item.name else "failed"
        results.append(TestResult(key=key, outcome=outcome, message=item.message[:200]))  # type: ignore[arg-type]
        if item.status == "failed":
            bodies[key] = _trim_lines(item.body)
    return results, bodies


# --------------------------------------------------------------------------- running


@contextmanager
def linked_node_modules(worktree: Path, root: Path):
    """Give the worktree the project's node_modules while tests run, then take the link away.

    The link must not exist while the worktree is committed or deleted: `git add -A` would record
    it and a careless cleanup could follow it into the project's real node_modules."""
    source = root / "node_modules"
    link = worktree / "node_modules"
    made = False
    if source.is_dir() and not link.exists() and not link.is_symlink():
        try:
            link.symlink_to(source, target_is_directory=True)
            made = True
        except OSError:
            made = False
    try:
        yield made
    finally:
        if made and link.is_symlink():
            link.unlink()


def _selection_files(
    project: ProjectConfig, worktree: Path, selection: Sequence[str] | None
) -> list[str]:
    if selection:
        return list(selection)
    return find_js_test_files(worktree)  # None or [] runs the whole suite


def run_js_tests(
    worktree: Path | str,
    project: ProjectConfig,
    root: Path | str,
    selection: Sequence[str] | None = None,
    timeout: float = 300.0,
    *,
    stage: Stage = "targeted",
    run_dir: Path | None = None,
    cancelled: Callable[[], bool] | None = None,
    env: dict[str, str] | None = None,
    node: str | None = None,
) -> PytestOutcome:
    """Run the project's JS/TS tests in `worktree`; the result looks like a pytest run."""
    worktree, root = Path(worktree), Path(root)
    temp_dir: Path | None = None
    if run_dir is None:
        temp_dir = Path(tempfile.mkdtemp(prefix="qed-jstest-"))
        run_dir = temp_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    report = run_dir / f"report-{stage}.json"
    report.unlink(missing_ok=True)
    log_path = run_dir / f"{stage}.log"
    runner = project.test_runner
    recorded = recorded_command(project, selection or [])

    def failure(status: Status, summary: str, details: str = "") -> PytestOutcome:
        fake = RunResult(args=recorded)
        check = _check(stage, False, summary, fake, [], details, None)
        return PytestOutcome(status, [], check)

    try:
        node_path = node or shutil.which("node") or ""
        if not node_path:
            return failure(
                "error", "Node.js was not found, so the tests could not run. Install Node 20+."
            )
        binary = None if runner == "node" else runner_binary(root, runner)
        if runner != "node" and binary is None:
            manager = project.package_manager or "npm"
            return failure(
                "error",
                f"{runner} is not installed in node_modules. Run `{manager} install` yourself "
                "(QEDCode never installs Node packages), then try again.",
            )
        files = _selection_files(project, worktree, selection)
        if not files:
            fake = RunResult(args=recorded, exit_code=0)
            return PytestOutcome(
                "no_tests",
                [],
                _check(stage, True, "No tests were collected.", fake, [], "", None),
                0,
            )
        command = real_command(project, node_path, binary, report, files)
        run_env = {
            "CI": "true",
            "NO_COLOR": "1",
            "FORCE_COLOR": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            **(env or {}),
        }
        with linked_node_modules(worktree, root):
            result = run(
                command,
                cwd=worktree,
                timeout=timeout,
                env=run_env,
                log_path=log_path,
                cancelled=cancelled,
            )
        result.args = recorded
        return _interpret(
            result,
            report,
            project,
            worktree,
            stage,
            timeout,
            log_path if temp_dir is None else None,
        )
    finally:
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)


def _interpret(
    result: RunResult,
    report: Path,
    project: ProjectConfig,
    worktree: Path,
    stage: Stage,
    timeout: float,
    log_path: Path | None,
) -> PytestOutcome:
    bodies: dict[str, str] = {}
    path = str(log_path) if log_path else None

    def outcome(
        status: Status, passed: bool, summary: str, results=None, details=""
    ) -> PytestOutcome:
        results = results or []
        check = _check(stage, passed, summary, result, results, details, path)
        return PytestOutcome(status, results, check, result.exit_code, result.duration_s, bodies)

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
        return outcome("error", False, f"The tests could not start: {result.error}"[:SUMMARY_LIMIT])
    text = ""
    try:
        text = report.read_text(encoding="utf-8")
    except OSError:
        pass
    if not text.strip():
        return outcome(
            "error",
            False,
            f"{project.test_runner} stopped without a report (exit {result.exit_code}).",
            details=_tail(result.output),
        )
    try:
        raw = (
            parse_node_report(text, worktree)
            if project.test_runner == "node"
            else parse_jest_report(text, worktree)
        )
    except (ValueError, TypeError):
        return outcome(
            "error", False, f"{project.test_runner} wrote a report that could not be read."
        )
    results, found = to_outcome(raw)
    bodies.update(found)
    ran = [r for r in results if r.outcome != "skipped"]
    failed = [r for r in results if r.outcome in ("failed", "error")]
    files_failed = [r for r in failed if "::" not in r.key]
    details = failure_report(found, results, [r.key for r in failed])
    if not ran:
        return outcome("no_tests", True, "No tests were collected.", results, _tail(result.output))
    if files_failed and len(files_failed) == len(failed):
        first = files_failed[0]
        return outcome(
            "collection_error",
            False,
            f"{len(files_failed)} test file(s) could not be loaded: {first.key}: "
            f"{_last_line(first.message)}"[:SUMMARY_LIMIT],
            results,
            details,
        )
    if failed:
        return outcome(
            "failed",
            False,
            f"{len(failed)} of {len(ran)} tests failed; first: {failed[0].key}"[:SUMMARY_LIMIT],
            results,
            details,
        )
    passed = sum(1 for r in results if r.outcome == "passed")
    return outcome("ok", True, f"{passed}/{len(ran)} tests pass.", results)


def _last_line(text: str) -> str:
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    for line in lines:
        if "Error" in line:
            return line[:150]
    return lines[0][:150] if lines else "load error"
