"""Shared helpers for tests that run the real cascade on bug zoo projects (real pytest, no GPU)."""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from qedcode.config import ProjectConfig
from qedcode.edits.protocol import parse_reply
from qedcode.models.schemas import Candidate, Event, Speed, TaskSpec
from qedcode.vcs.snapshot import snapshot
from qedcode.verify.baseline import baseline_run, fails_on_base
from qedcode.verify.cascade import CascadeContext, ReproTest
from tests.conftest import BUGZOO_DIR, run_git

PY = sys.executable
REPRO_PATH = "tests/test_qed_repro.py"


@dataclass
class ZooCase:
    project: Path
    ctx: CascadeContext
    events: list[Event] = field(default_factory=list)
    edits_text: str = ""
    hidden_text: str = ""

    def candidate(self, text: str | None = None, cid: str = "c1", role: str = "coder") -> Candidate:
        return Candidate(id=cid, role=role, reply=parse_reply(text or self.edits_text))  # type: ignore[arg-type]

    def stages(self, cid: str = "c1") -> list[tuple[str, bool]]:
        return [
            (e.data["stage"], e.data["passed"])
            for e in self.events
            if e.kind == "check" and e.data.get("candidate") == cid and "passed" in e.data
        ]


def zoo_case(
    tmp_path: Path,
    name: str = "001_paginate_off_by_one",
    *,
    speed: Speed = Speed.NORMAL,
    repro: bool = True,
    full_baseline: bool = True,
    **ctx_overrides,
) -> ZooCase:
    """A git repo of one bug zoo project with a snapshot, a baseline and an optional repro test."""
    project = tmp_path / "zoo" / name
    shutil.copytree(
        BUGZOO_DIR / name, project, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache")
    )
    hidden_text = (project / "hidden" / "test_hidden.py").read_text(encoding="utf-8")
    edits_text = (project / "edits.txt").read_text(encoding="utf-8")
    shutil.rmtree(project / "hidden")
    (project / "edits.txt").unlink()
    (project / "solution.patch").unlink()
    (project / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n")
    run_git(project, "init", "-q", "-b", "main")
    run_git(project, "add", "-A")
    run_git(project, "commit", "-q", "-m", "buggy")
    task = TaskSpec(id="t-20261120-000000-zoo1", request="fix it", speed=speed)
    snap = snapshot(project, task.id)
    config = ProjectConfig(python=PY, confirmed=True)
    run_dir = project / ".qed" / "runs" / task.id
    base = baseline_run(project, snap, config, full=full_baseline, related=[], run_dir=run_dir)
    repro_test = None
    if repro:
        failures = fails_on_base(project, snap, config, REPRO_PATH, hidden_text, run_dir=run_dir)
        repro_test = ReproTest(REPRO_PATH, hidden_text, failures or [])
    events: list[Event] = []
    ctx = CascadeContext(
        root=project,
        base_commit=snap,
        baseline=base.baseline,
        project=config,
        task=task,
        run_dir=run_dir,
        baseline_full_seconds=base.full_seconds,
        repro=repro_test,
        on_event=events.append,
    )
    for key, value in ctx_overrides.items():
        setattr(ctx, key, value)
    return ZooCase(project, ctx, events, edits_text, hidden_text)
