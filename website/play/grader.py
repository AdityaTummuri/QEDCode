"""Fool the Grader: grade a player's edit of a bug zoo level with QEDCode's own rules.

Runs inside Pyodide in the browser (and under CPython in QEDCode's tests). The verdict comes from
the real `verify.guard.scope_check`, `verify.guard.risk_check`, `verify.grade.assign_grade` and
`verify.receipt` (summary line, next step, receipt link), so every cheat gets the wording
`qed check` prints. Like `qed check`, the player's change is graded as the role "user".
Differences from the CLI, said on the page: no lint stage (ruff does not run in a browser) and a
hidden test that Benchy-style judges the result.
"""

from __future__ import annotations

import ast
import difflib
import json
import os
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from qedcode.config import ProjectConfig
from qedcode.models.schemas import (
    Baseline,
    Candidate,
    CheckResult,
    Speed,
    TaskKind,
    TaskSpec,
    TestResult,
)
from qedcode.project.paths import is_test_file
from qedcode.verify.grade import assign_grade
from qedcode.verify.guard import risk_check, scope_check
from qedcode.verify.receipt import build_receipt, next_step, receipt_link, summary_line

sys.dont_write_bytecode = True


def git_diff(base: dict[str, str], work: dict[str, str]) -> str:
    """A git-style unified diff of every file that differs (added, changed or deleted)."""
    out: list[str] = []
    for path in sorted(set(base) | set(work)):
        old, new = base.get(path), work.get(path)
        if old == new:
            continue
        out.append(f"diff --git a/{path} b/{path}\n")
        if old is None:
            out.append("new file mode 100644\n")
        if new is None:
            out.append("deleted file mode 100644\n")
        a = (old or "").splitlines(keepends=True)
        b = (new or "").splitlines(keepends=True)
        for line in difflib.unified_diff(
            a,
            b,
            "/dev/null" if old is None else f"a/{path}",
            "/dev/null" if new is None else f"b/{path}",
        ):
            out.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return "".join(out)


def _functions(source: str) -> dict[str, str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    return {
        n.name: ast.dump(n)
        for n in tree.body
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name.startswith("test")
    }


def appended_tests(base: dict[str, str], work: dict[str, str]) -> dict[str, set[str]]:
    """Existing test files that only gained new test functions (evidence, not tampering)."""
    found: dict[str, set[str]] = {}
    for path, new in work.items():
        old = base.get(path)
        if old is None or old == new or not is_test_file(path):
            continue
        before, after = _functions(old), _functions(new)
        if before and all(after.get(name) == body for name, body in before.items()):
            try:
                same_rest = [
                    ast.dump(n) for n in ast.parse(old).body if not isinstance(n, ast.FunctionDef)
                ] == [
                    ast.dump(n) for n in ast.parse(new).body if not isinstance(n, ast.FunctionDef)
                ]
            except SyntaxError:
                same_rest = False
            added = set(after) - set(before)
            if added and same_rest:
                found[path] = added
    return found


def code_changed(base: dict[str, str], work: dict[str, str]) -> bool:
    """Did anything outside tests change by syntax tree (not only comments or formatting)?"""
    for path in set(base) | set(work):
        if is_test_file(path) or not path.endswith(".py"):
            continue
        old, new = base.get(path), work.get(path)
        if old == new:
            continue
        try:
            if old is None or new is None or ast.dump(ast.parse(old)) != ast.dump(ast.parse(new)):
                return True
        except SyntaxError:
            return True
    return False


def _write(folder: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        target = folder / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def run_pytest(folder: Path, selection: list[str]) -> dict[str, str]:
    """Run pytest in this process on `selection` inside `folder`; key -> outcome."""
    import pytest

    folder = folder.resolve()
    for name, module in list(sys.modules.items()):
        where = getattr(module, "__file__", None) or ""
        if where and str(Path(where).resolve()).startswith(str(folder.parent)):
            del sys.modules[name]
    report = folder / ".qed-report.xml"
    report.unlink(missing_ok=True)
    saved_path, saved_cwd = list(sys.path), os.getcwd()
    sys.path[:0] = [str(folder / "src"), str(folder)]
    os.chdir(folder)
    try:
        pytest.main(
            [
                "-q",
                "-p",
                "no:cacheprovider",
                "--import-mode=importlib",
                f"--junitxml={report}",
                "--rootdir",
                str(folder),
                *selection,
            ]
        )
    finally:
        sys.path[:] = saved_path
        os.chdir(saved_cwd)
    outcomes: dict[str, str] = {}
    if not report.exists():
        return outcomes
    for case in ET.parse(report).getroot().iter("testcase"):
        key = f"{case.get('classname', '')}::{case.get('name', '')}"
        if case.find("failure") is not None:
            outcomes[key] = "failed"
        elif case.find("error") is not None:
            outcomes[key] = "error"
        elif case.find("skipped") is not None:
            outcomes[key] = "skipped"
        else:
            outcomes[key] = "passed"
    return outcomes


def _results(outcomes: dict[str, str]) -> list[TestResult]:
    return [TestResult(key=k, outcome=v) for k, v in sorted(outcomes.items())]  # type: ignore[arg-type]


def _new_test_files(
    base: dict[str, str], work: dict[str, str], appended: dict[str, set[str]]
) -> list[str]:
    added = [p for p in work if p not in base and is_test_file(p) and p.endswith(".py")]
    return sorted(added + list(appended))


def grade_level(level: dict, edits: dict[str, str]) -> dict:
    """Grade the player's files for one level. `edits` are the full texts of changed files."""
    base: dict[str, str] = dict(level["files"])
    work = {**base, **{k: v for k, v in edits.items() if v is not None}}
    for path, text in edits.items():
        if text is None:
            work.pop(path, None)
    root = Path(tempfile.mkdtemp(prefix="qed-play-"))
    try:
        return _grade(level, base, work, root)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _grade(level: dict, base: dict[str, str], work: dict[str, str], root: Path) -> dict:
    diff = git_diff(base, work)
    if not diff:
        return {
            "status": "no_changes",
            "message": "Nothing changed yet. Edit the code or the tests, then grade it.",
        }
    cand = Candidate(id="u1", role="user", diff=diff)
    stages: list[CheckResult] = []
    notes: list[str] = []

    def record(check: CheckResult) -> bool:
        stages.append(check)
        cand.checks.append(check)
        if not check.passed:
            cand.failed_stage = check.stage
        return check.passed

    changed_py = [p for p in work if p.endswith(".py") and work.get(p) != base.get(p)]
    broken = []
    for path in changed_py:
        try:
            compile(work[path], path, "exec")
        except SyntaxError as exc:
            broken.append(f"{path} line {exc.lineno}: {exc.msg}")
    parse_ok = record(
        CheckResult(
            stage="parse",
            passed=not broken,
            summary=f"Syntax error in {broken[0]}."
            if broken
            else f"{len(changed_py)} changed file(s) parse.",
        )
    )
    appended = appended_tests(base, work)
    if parse_ok:
        record(scope_check(diff, ProjectConfig(), "user", Speed.NORMAL, appended=appended))
        record(risk_check(diff))
    cand.code_changed = code_changed(base, work)
    base_dir, work_dir = root / "base", root / "work"
    _write(base_dir, base)
    _write(work_dir, work)
    if cand.failed_stage is None:
        new_tests = _new_test_files(base, work, appended)
        if new_tests:
            _write(base_dir, {p: work[p] for p in new_tests})
            before = run_pytest(base_dir, new_tests)
            after = run_pytest(work_dir, new_tests)
            proof = {
                k: v
                for k, v in after.items()
                if v == "passed" and before.get(k) in ("failed", "error")
            }
            for path, names in appended.items():  # only the added functions count as proof
                dotted = path[:-3].replace("/", ".")
                proof = {
                    k: v
                    for k, v in proof.items()
                    if not k.startswith(dotted + "::") or k.split("::")[-1] in names
                }
            all_pass = bool(after) and all(v == "passed" for v in after.values())
            if not all_pass:
                record(
                    CheckResult(
                        stage="fbpa",
                        passed=False,
                        summary="The new test does not pass with the change.",
                    )
                )
            elif proof:
                record(
                    CheckResult(
                        stage="fbpa",
                        passed=True,
                        tests=_results(proof),
                        summary=f"{len(proof)} test(s) failed before the change and pass now.",
                    )
                )
            else:  # as in the cascade: no proof is a warning, not a passed check
                notes.append("The new test already passed before the change, so it proves nothing.")
    if cand.failed_stage is None:
        tests = [p for p in work if is_test_file(p) and p.endswith(".py")]
        outcomes = run_pytest(work_dir, tests)
        ran = [v for v in outcomes.values() if v != "skipped"]
        failing = [k for k, v in outcomes.items() if v in ("failed", "error")]
        record(
            CheckResult(
                stage="full",
                passed=not failing,
                tests=_results(outcomes),
                summary=f"{ran.count('passed')}/{len(ran)} tests pass."
                if not failing
                else f"{len(failing)} test(s) fail: {', '.join(failing[:3])}",
            )
        )
        cand.tests_passed = ran.count("passed")
        cand.full_suite_ran = True
    grade = assign_grade(cand, Baseline(commit="base", full=True))
    _write(work_dir, level["hidden"])
    hidden = run_pytest(work_dir, list(level["hidden"])) if not broken else {}
    hidden_ok = bool(hidden) and all(v == "passed" for v in hidden.values())
    out = {
        "status": "graded" if grade else "rejected",
        "grade": grade.value if grade else None,
        "stages": [
            {"stage": c.stage, "passed": c.passed, "summary": c.summary, "details": c.details}
            for c in stages
        ],
        "hidden": {"passed": hidden_ok, "ran": len(hidden)},
        "won": bool(grade and grade.value == "A" and hidden_ok),
        "notes": notes,
    }
    if grade is None:
        last = stages[-1] if stages else None
        out["summary"] = f"Rejected at {cand.failed_stage}: {last.summary if last else ''}"
        out["next_step"] = ""
        return out
    receipt = build_receipt(
        root=work_dir,
        task=TaskSpec(id="t-play", request=level["task"], kind=TaskKind.BUGFIX),
        cand=cand,
        baseline=Baseline(commit="base", full=True),
        project=ProjectConfig(python="python"),
        base_commit="0" * 40,
        base_ref="the level's code",
    )
    receipt.unverified = [g for g in receipt.unverified if "network blocking" not in g] + [
        "Lint did not run (ruff does not run in a browser)."
    ]
    out["summary"] = summary_line(receipt).replace(" · lint not run", "")
    out["next_step"] = next_step(receipt)
    try:
        out["link"] = receipt_link(receipt)
    except ValueError:
        out["link"] = ""
    return out


def main_json(payload: str) -> str:
    data = json.loads(payload)
    return json.dumps(grade_level(data["level"], data["edits"]))
