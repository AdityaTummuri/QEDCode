"""Prove that every bug zoo project is valid.

For each project, in a temporary copy:
  1. the visible tests must PASS on the buggy code,
  2. the hidden tests must FAIL on the buggy code,
  3. after applying solution.patch, both must PASS.

Run it with `uv run python bugzoo/check_zoo.py`. It prints a table and exits 1 if any project is
invalid. It runs real pytest (fast, no GPU, no network).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ZOO_DIR = Path(__file__).resolve().parent
_IGNORE = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc")


@dataclass
class ZooResult:
    name: str
    visible_passes_before: bool = False
    hidden_fails_before: bool = False
    patch_applies: bool = False
    visible_passes_after: bool = False
    hidden_passes_after: bool = False
    problems: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.problems and all(
            (
                self.visible_passes_before,
                self.hidden_fails_before,
                self.patch_applies,
                self.visible_passes_after,
                self.hidden_passes_after,
            )
        )


def list_projects(zoo_dir: Path = ZOO_DIR) -> list[Path]:
    """Return the bug zoo project folders (NNN_name), sorted."""
    return sorted(p for p in zoo_dir.iterdir() if p.is_dir() and p.name[:3].isdigit())


def run_pytest(cwd: Path, *args: str, timeout: int = 120) -> int:
    """Run pytest in `cwd` and return its exit code (0 pass, 1 fail, 2 error, 5 none collected)."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    env.pop("PYTEST_ADDOPTS", None)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode


def _git_apply(cwd: Path, patch: Path) -> bool:
    subprocess.run(["git", "init", "-q"], cwd=cwd, check=True, capture_output=True)
    proc = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", str(patch)],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def check_project(project: Path) -> ZooResult:
    """Check one project in a temporary copy and report which promises hold."""
    result = ZooResult(name=project.name)
    for required in ("task.txt", "solution.patch", "edits.txt", "hidden/test_hidden.py"):
        if not (project / required).exists():
            result.problems.append(f"missing {required}")
    if result.problems:
        return result
    with tempfile.TemporaryDirectory(prefix="zoo-") as tmp:
        work = Path(tmp) / project.name
        shutil.copytree(project, work, ignore=_IGNORE)
        result.visible_passes_before = run_pytest(work) == 0
        hidden_rc = run_pytest(work, "hidden/test_hidden.py")
        result.hidden_fails_before = hidden_rc not in (0, 5)
        if not result.visible_passes_before:
            result.problems.append("visible tests do not pass on the buggy code")
        if not result.hidden_fails_before:
            result.problems.append("hidden tests do not fail on the buggy code")
        result.patch_applies = _git_apply(work, work / "solution.patch")
        if not result.patch_applies:
            result.problems.append("solution.patch does not apply")
            return result
        result.visible_passes_after = run_pytest(work) == 0
        result.hidden_passes_after = run_pytest(work, "hidden/test_hidden.py") == 0
        if not result.visible_passes_after:
            result.problems.append("visible tests fail after the fix")
        if not result.hidden_passes_after:
            result.problems.append("hidden tests fail after the fix")
    return result


def main() -> int:
    from rich.console import Console
    from rich.table import Table

    projects = list_projects()
    results = [check_project(p) for p in projects]
    table = Table(title="Bug zoo")
    table.add_column("project", no_wrap=True)
    for col in ("visible ok", "hidden fails", "patch", "visible after", "hidden after", "valid"):
        table.add_column(col, justify="center")

    def mark(flag: bool) -> str:
        return "yes" if flag else "NO"

    for r in results:
        table.add_row(
            r.name,
            mark(r.visible_passes_before),
            mark(r.hidden_fails_before),
            mark(r.patch_applies),
            mark(r.visible_passes_after),
            mark(r.hidden_passes_after),
            mark(r.valid),
        )
    console = Console()
    console.print(table)
    bad = [r for r in results if not r.valid]
    for r in bad:
        for problem in r.problems:
            console.print(f"[red]{r.name}: {problem}[/red]")
    valid = len(results) - len(bad)
    console.print(f"{valid} of {len(results)} projects valid")
    return 1 if bad or not results else 0


if __name__ == "__main__":
    sys.exit(main())
