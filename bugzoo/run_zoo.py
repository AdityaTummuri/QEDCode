"""Run `qed solve` on the bug zoo with your configured model and report how it did.

This is the "real-model check" of the build plan (step 5.4): for each project it makes a private git
copy (without the hidden tests), solves the task written in task.txt, applies a successful result
to the copy, and then runs the hidden test to see whether a "passed" fix was really right.

    uv run python bugzoo/run_zoo.py --speed normal
    uv run python bugzoo/run_zoo.py --speed fast --only 001,002,003
    uv run python bugzoo/run_zoo.py --json > results.json

It uses the model from ~/.qedcode/config.toml, so it needs `qed setup` first. It never touches the
bug zoo folders themselves.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

ZOO_DIR = Path(__file__).resolve().parent


@dataclass
class ZooRun:
    project: str
    status: str
    grade: str
    seconds: float
    hidden_passes: bool | None  # None when nothing was applied
    message: str


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=zoo", "-c", "user.email=zoo@localhost", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def prepare_copy(project: Path, target: Path) -> Path:
    """A git repo with the project as a user would have it (no hidden tests, no answer files)."""
    shutil.copytree(project, target, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    for name in ("hidden", "edits.txt", "solution.patch"):
        path = target / name
        shutil.rmtree(path) if path.is_dir() else path.unlink(missing_ok=True)
    (target / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n", encoding="utf-8")
    _git(target, "init", "-q", "-b", "main")
    _git(target, "add", "-A")
    _git(target, "commit", "-q", "-m", "buggy")
    return target


def hidden_test_passes(project: Path, applied_copy: Path) -> bool:
    """Run the project's hidden test against the copy that received the fix."""
    shutil.copytree(project / "hidden", applied_copy / "hidden")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "hidden/test_hidden.py"],
        cwd=applied_copy,
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def run_project(project: Path, speed: str, client_factory: Callable | None = None) -> ZooRun:
    from qedcode.config import ProjectConfig, load_global, save_project
    from qedcode.engine import api
    from qedcode.models.schemas import Speed
    from qedcode.runtime.client import make_client

    with tempfile.TemporaryDirectory(prefix="zoo-run-") as tmp:
        work = prepare_copy(project, Path(tmp) / project.name)
        save_project(work, ProjectConfig(python=sys.executable, confirmed=True))
        config = load_global()
        client = client_factory(project.name) if client_factory else make_client(config, "main")
        started = time.monotonic()
        try:
            result = api.solve(
                work,
                (project / "task.txt").read_text(encoding="utf-8").strip(),
                speed=Speed(speed),
                config=config,
                client=client,
            )
        finally:
            if client_factory is None:
                client.unload()
        seconds = time.monotonic() - started
        grade = result.best.grade.value if result.best and result.best.grade else "-"
        correct: bool | None = None
        if result.status == "done" and result.best is not None:
            applied = api.apply_result(work, result.task.id, allow_low_grade=True)
            if applied.status == "applied":
                correct = hidden_test_passes(project, work)
        return ZooRun(
            project.name, result.status, grade, round(seconds, 1), correct, result.message
        )


def summarize(runs: list[ZooRun]) -> dict:
    graded = [r for r in runs if r.grade != "-"]
    return {
        "projects": len(runs),
        "grade_A": sum(r.grade == "A" for r in runs),
        "grade_B": sum(r.grade == "B" for r in runs),
        "grade_C_or_D": sum(r.grade in ("C", "D") for r in runs),
        "no_fix": sum(r.status != "done" for r in runs),
        "wrong_but_graded_A_or_B": sum(
            r.hidden_passes is False and r.grade in ("A", "B") for r in runs
        ),
        "mean_seconds": round(sum(r.seconds for r in runs) / max(len(runs), 1), 1),
        "graded": len(graded),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run qed solve on the bug zoo.")
    parser.add_argument("--speed", default="normal", choices=["fast", "normal", "sure"])
    parser.add_argument("--only", default="", help="comma-separated project numbers, e.g. 001,002")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = parser.parse_args(argv)
    wanted = {part.strip() for part in args.only.split(",") if part.strip()}
    projects = sorted(
        p
        for p in ZOO_DIR.iterdir()
        if p.is_dir() and p.name[:3].isdigit() and (not wanted or p.name[:3] in wanted)
    )
    runs = []
    for project in projects:
        print(f"Running {project.name} ...", file=sys.stderr)
        runs.append(run_project(project, args.speed))
    summary = summarize(runs)
    if args.json:
        print(
            json.dumps(
                {"speed": args.speed, "runs": [asdict(r) for r in runs], "summary": summary},
                indent=1,
            )
        )
        return 0
    from rich.console import Console
    from rich.table import Table

    table = Table(title=f"Bug zoo at {args.speed} speed")
    for column in ("project", "result", "grade", "seconds", "hidden test"):
        table.add_column(column, no_wrap=column == "project")
    for r in runs:
        hidden = "n/a" if r.hidden_passes is None else ("passes" if r.hidden_passes else "FAILS")
        table.add_row(r.project, r.status, r.grade, str(r.seconds), hidden)
    Console().print(table)
    print(
        f"A: {summary['grade_A']}  B: {summary['grade_B']}  C/D: {summary['grade_C_or_D']}  "
        f"no fix: {summary['no_fix']}  wrong A/B (a bug to fix): "
        f"{summary['wrong_but_graded_A_or_B']}  "
        f"mean time: {summary['mean_seconds']}s"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
