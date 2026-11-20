"""Static stages: `parse` (ast.parse on changed files) and `lint` (new fatal ruff errors only).

Lint runs ruff's fatal rules (E9, F63, F7, F82: syntax errors, undefined names, misuse of
`return`/`break`, invalid comparisons) on the base and the candidate version of every changed
Python file. The candidate fails only if some rule code appears MORE often than in the base, so
old problems in a messy project are never blamed on a new change.
"""

from __future__ import annotations

import ast
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

from qedcode.edits import js_symbols
from qedcode.models.schemas import CheckResult
from qedcode.vcs.git import git
from qedcode.verify.run import run

FATAL_RULES = "E9,F63,F7,F82"
SUMMARY_LIMIT = 300
DETAILS_LIMIT = 4000


def parse_check(worktree: Path | str, changed_files: list[str]) -> CheckResult:
    """Stage `parse`: every changed .py file must be valid Python, every JS/TS file valid too."""
    worktree = Path(worktree)
    problems: list[str] = []
    checked = 0
    for rel in changed_files:
        path = worktree / rel
        lang = js_symbols.language_for(rel)
        if lang is not None:
            if not path.is_file():
                continue
            checked += 1
            error = js_symbols.syntax_error(path.read_bytes(), lang)
            if error:
                problems.append(f"{rel}, {error}")
            continue
        if not rel.endswith(".py"):
            continue
        if not path.is_file():
            continue
        checked += 1
        try:
            ast.parse(path.read_bytes(), filename=rel)
        except SyntaxError as exc:
            problems.append(f"{rel}, line {exc.lineno}: {exc.msg}")
        except ValueError as exc:  # null bytes and the like
            problems.append(f"{rel}: {exc}")
    if problems:
        return CheckResult(
            stage="parse",
            passed=False,
            summary=f"Syntax error in {problems[0]}"[:SUMMARY_LIMIT],
            details="Syntax errors:\n" + "\n".join(problems)[:DETAILS_LIMIT],
        )
    summary = f"{checked} changed file(s) parse." if checked else "No source files changed."
    return CheckResult(stage="parse", passed=True, summary=summary)


def _ruff(files: dict[str, str]) -> tuple[list[dict] | None, str]:
    """Run ruff's fatal rules on `files` (relative path -> text). None when ruff cannot run."""
    if not files:
        return [], ""
    with tempfile.TemporaryDirectory(prefix="qed-lint-") as tmp:
        root = Path(tmp)
        for rel, text in files.items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="")
        result = run(
            [
                sys.executable,
                "-m",
                "ruff",
                "check",
                "--isolated",
                "--select",
                FATAL_RULES,
                "--output-format",
                "json",
                "--no-cache",
                *files,
            ],
            cwd=root,
            timeout=120,
        )
    if result.error or result.exit_code not in (0, 1):
        return None, result.error or result.output[:300]
    try:
        return json.loads(result.stdout or "[]"), ""
    except json.JSONDecodeError:
        return None, "ruff printed output that could not be read"


def _code(diag: dict) -> str:
    return str(diag.get("code") or "syntax-error")


def _describe(diag: dict, root: str = "") -> str:
    row = (diag.get("location") or {}).get("row", "?")
    name = str(diag.get("filename", ""))
    if root and name.startswith(root):
        name = name[len(root) :].lstrip("/\\")
    return f"{name}:{row} {_code(diag)} {diag.get('message', '')}"


def base_texts(root: Path | str, base_commit: str, changed_files: list[str]) -> dict[str, str]:
    """The base version of each changed .py file (files that did not exist are left out)."""
    texts: dict[str, str] = {}
    for rel in changed_files:
        if not rel.endswith(".py"):
            continue
        shown = git(root, "show", f"{base_commit}:{rel}", exact=True)
        if shown.ok:
            texts[rel] = shown.stdout
    return texts


def lint_check(
    worktree: Path | str,
    root: Path | str,
    base_commit: str,
    changed_files: list[str],
) -> CheckResult:
    """Stage `lint`: fail when a fatal rule appears more often in the candidate than in the base."""
    worktree = Path(worktree)
    py_files = [f for f in changed_files if f.endswith(".py") and (worktree / f).is_file()]
    if not py_files:
        return CheckResult(stage="lint", passed=True, summary="No Python files to lint.")
    candidate = {f: (worktree / f).read_text(encoding="utf-8", errors="replace") for f in py_files}
    new, why_new = _ruff(candidate)
    old, why_old = _ruff(base_texts(root, base_commit, py_files))
    if new is None or old is None:
        return CheckResult(
            stage="lint",
            passed=True,
            summary="Lint could not run, so it was skipped.",
            details=f"ruff problem: {why_new or why_old}. Run `qed doctor` to check ruff.",
        )
    before, after = Counter(_code(d) for d in old), Counter(_code(d) for d in new)
    grown = sorted(code for code, n in after.items() if n > before.get(code, 0))
    if not grown:
        return CheckResult(
            stage="lint",
            passed=True,
            summary=f"No new serious lint errors in {len(py_files)} file(s).",
        )
    lines = [_describe(d) for d in new if _code(d) in grown]
    return CheckResult(
        stage="lint",
        passed=False,
        summary=f"New lint error {grown[0]} ({lines[0]})"[:SUMMARY_LIMIT],
        details=("New serious lint errors:\n" + "\n".join(lines))[:DETAILS_LIMIT],
    )
