"""One entry point for running a project's tests, whatever the language (AGENTS.md 15.3).

Python projects run pytest (optionally in the sealed test box); JavaScript and TypeScript
projects run node, Vitest or Jest. Both return the same `PytestOutcome`, so the baseline, the
cascade and `qed verify` do not care which one ran.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from qedcode.config import ProjectConfig
from qedcode.models.schemas import Stage
from qedcode.project.detect import expand_test_command
from qedcode.verify.js_runner import run_js_tests
from qedcode.verify.pytest_runner import PytestOutcome, run_pytest
from qedcode.verify.sandbox import sandbox_for


def run_tests(
    worktree: Path | str,
    project: ProjectConfig,
    root: Path | str,
    selection: Sequence[str] | None = None,
    timeout: float = 300.0,
    *,
    stage: Stage = "targeted",
    run_dir: Path | None = None,
    cancelled: Callable[[], bool] | None = None,
    command_prefix: Sequence[str] | None = None,
    env: dict[str, str] | None = None,
) -> PytestOutcome:
    """Run `selection` (files or test ids; None or [] means everything) in `worktree`.

    `root` is the user's project folder (JavaScript runs borrow its node_modules, the sealed
    test box is chosen from its settings). `command_prefix` replaces the configured pytest
    command (used by `qed verify` to re-run exactly what a receipt recorded)."""
    if project.language == "javascript":
        return run_js_tests(
            worktree,
            project,
            root,
            selection,
            timeout,
            stage=stage,
            run_dir=run_dir,
            cancelled=cancelled,
            env=env,
        )
    return run_pytest(
        worktree,
        project.python or sys.executable,
        selection,
        timeout,
        stage=stage,
        run_dir=run_dir,
        command_prefix=command_prefix
        if command_prefix is not None
        else expand_test_command(project),
        cancelled=cancelled,
        env=env,
        sandbox=sandbox_for(project, root),
    )
