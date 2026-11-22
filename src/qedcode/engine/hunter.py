"""Bug Hunter (`qed hunt`, AGENTS.md section 15.12).

The model suspects bugs and writes tests of the correct behaviour. A finding survives only if its
test fails on today's code; a normal solve then runs with that test as the repro, so each finding
ends with a proven fix or as "not fixed". Nothing is applied.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from qedcode.engine.check import project_config_for
from qedcode.engine.lock import ProjectBusy, project_lock
from qedcode.engine.roles import Roles
from qedcode.engine.safety_net import TARGETS, Target, _test_name, keep_only, public_targets
from qedcode.models.ids import new_task_id
from qedcode.models.schemas import Callbacks, Event, SolveResult, Speed
from qedcode.project.context import ContextFile, read_project_file
from qedcode.project.detect import find_test_files
from qedcode.runtime.client import ModelClient
from qedcode.vcs import worktree
from qedcode.vcs.git import GitError
from qedcode.vcs.snapshot import snapshot
from qedcode.verify.cascade import Cancelled
from qedcode.verify.runners import run_tests

SolveFn = Callable[[str, tuple[str, str]], SolveResult]


@dataclass
class Finding:
    target: Target
    test_name: str
    description: str
    test_path: str
    test_code: str
    result: SolveResult | None = None

    @property
    def fixed(self) -> bool:
        best = self.result.best if self.result else None
        return bool(best and best.grade is not None and best.grade.value == "A")


@dataclass
class HuntResult:
    status: str  # "done", "nothing", "error", "cancelled"
    message: str = ""
    findings: list[Finding] = field(default_factory=list)
    looked_at: list[str] = field(default_factory=list)


def hunt_test_path(target: Target) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", f"{Path(target.path).stem}_{target.name}".lower()).strip("_")
    return f"tests/test_qed_hunt_{slug}.py"


def docstrings(code: str) -> dict[str, str]:
    """test name -> its one-line docstring (the suspicion)."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return {}
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
            "test"
        ):
            doc = (ast.get_docstring(node) or "").strip().splitlines()
            out[node.name] = doc[0] if doc else node.name.replace("_", " ")
    return out


def _suspect(
    roles: Roles, root: Path, target: Target, example, wt: Path, project, cb
) -> list[Finding]:
    """Ask for suspicious tests of one target and keep those that fail on today's code."""
    path = hunt_test_path(target)
    source = read_project_file(root, target.path) or ""
    cb.on_event(Event(kind="stage", message=f"Looking for bugs in {target.symbol}..."))
    written = roles.hunter(
        target.symbol, target.path, [ContextFile(target.path, source)], example, path
    )
    if written is None:
        return []
    (wt / path).parent.mkdir(parents=True, exist_ok=True)
    (wt / path).write_text(written.content, encoding="utf-8")
    out = run_tests(
        wt, project, root, [path], float(project.test_timeout_seconds), cancelled=cb.cancelled
    )
    (wt / path).unlink(missing_ok=True)
    if out.status in ("collection_error", "timeout", "cancelled", "error") and not out.results:
        return []
    failing = {_test_name(r.key) for r in out.failing}
    notes = docstrings(written.content)
    return [
        Finding(target, name, notes.get(name, name), path, keep_only(written.content, {name}))
        for name in sorted(failing)
        if name in notes
    ]


def hunt(
    root: Path,
    path: str,
    speed: Speed,
    cb: Callbacks,
    client: ModelClient,
    solve_fn: SolveFn,
) -> HuntResult:
    """Find suspected bugs whose tests fail today, then try to prove a fix for each."""
    task_id = new_task_id()
    notes: list[str] = []
    try:
        with project_lock(root, task_id):
            snap = snapshot(root, task_id)
            project = project_config_for(root, notes)
            if project.language != "python":
                return HuntResult("error", "The Bug Hunter works on Python projects for now.")
            targets = public_targets(root, path, TARGETS[speed])
            if not targets:
                return HuntResult("nothing", "There are no public Python functions to look at.")
            roles = Roles(
                client,
                root / ".qed" / "runs" / task_id,
                on_event=cb.on_event,
                cancelled=cb.cancelled,
                project=project,
            )
            tests = find_test_files(root, "python")
            text = read_project_file(root, tests[0]) if tests else None
            example = ContextFile(tests[0], text) if tests and text is not None else None
            wt = worktree.create(root, snap, f"{task_id}-hunt")
            try:
                findings = [
                    f for t in targets for f in _suspect(roles, root, t, example, wt, project, cb)
                ]
            finally:
                worktree.remove(root, wt)
    except ProjectBusy as exc:
        return HuntResult("error", str(exc))
    except Cancelled:
        return HuntResult("cancelled", "Cancelled.")
    except GitError as exc:
        return HuntResult("error", str(exc))
    looked = [f"{t.path}:{t.symbol}" for t in targets]
    if not findings:
        return HuntResult(
            "nothing", "No suspected bug was confirmed by a failing test.", [], looked
        )
    for finding in findings[: TARGETS[speed]]:  # each fix is a full solve: keep the count small
        if cb.cancelled():
            break
        request = (
            f"Bug in {finding.target.symbol} ({finding.target.path}): {finding.description}. "
            f"The test {finding.test_path}::{finding.test_name} shows it."
        )
        cb.on_event(Event(kind="stage", message=f"Fixing: {finding.description}"))
        finding.result = solve_fn(request, (finding.test_path, finding.test_code))
    fixed = sum(1 for f in findings if f.fixed)
    message = (
        f"{len(findings)} suspected bug(s) confirmed by a failing test, {fixed} with a proven fix. "
        "Check each test's expected behaviour before applying: a test can encode a wrong "
        "expectation."
    )
    return HuntResult("done", message, findings, looked)
