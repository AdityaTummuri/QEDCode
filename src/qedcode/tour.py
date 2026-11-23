"""`qed try`: the whole loop on a tiny bundled project, so anyone can see a verified grade A.

A front door like `benchy_link.py`: it only calls the engine API, and the engine never imports it.
The bundled projects are copies of bug zoo projects (`tour_data/`); everything happens in a
temporary folder, never in the user's current folder (AGENTS.md section 15.7).
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from qedcode.config import ProjectConfig, global_config_path, save_project
from qedcode.engine import api
from qedcode.engine.check import CheckOutcome
from qedcode.models.schemas import Callbacks, SolveResult, Speed
from qedcode.runtime.client import ModelClient
from qedcode.runtime.fake import FakeModel, load_script
from qedcode.vcs.git import GitError, git
from qedcode.verify.receipt import load_receipt, test_counts
from qedcode.verify.verify import VerifyResult

TOUR_DIR = Path(__file__).parent / "tour_data"
DEFAULT_PROJECT = "001_paginate_off_by_one"
ACTION_DOCS = "https://github.com/AdityaTummuri/QEDCode/blob/main/docs/github-action.md"
SCRIPTED_NOTE = "scripted: shows the engine, not model quality"


class TourError(RuntimeError):
    """The demo could not start. The message says what happened and what to do next."""


@dataclass
class TourResult:
    project: Path
    task: str
    scripted: bool
    solve: SolveResult
    verify: VerifyResult | None = None
    notes: list[str] = field(default_factory=list)


def list_projects() -> list[str]:
    """The bundled demo projects, by folder name (`001_paginate_off_by_one`, ...)."""
    return sorted(p.name for p in TOUR_DIR.iterdir() if (p / "task.txt").is_file())


def find_project(name: str | None) -> str:
    """Resolve a project name or its number prefix (`7`, `007`, `sort`) to a folder name."""
    names = list_projects()
    if not name:
        return DEFAULT_PROJECT
    wanted = name.strip().lower()
    for candidate in names:
        number = candidate.split("_", 1)[0]
        if wanted in (candidate, number) or wanted.lstrip("0") == number.lstrip("0"):
            return candidate
    matches = [c for c in names if wanted in c]
    if len(matches) == 1:
        return matches[0]
    raise TourError(
        f"There is no demo project called '{name}'. Choose one of: {', '.join(names)} "
        "(see `qed try --list`)."
    )


def task_text(name: str) -> str:
    return (TOUR_DIR / name / "task.txt").read_text(encoding="utf-8").strip()


def scripted_model(name: str) -> FakeModel:
    """The project's scripted stand-in model (try 1 cheats, try 2 is the real fix)."""
    return FakeModel(load_script(TOUR_DIR / name / "script.yaml"), name="scripted demo model")


def has_model_setup() -> bool:
    """True once `qed setup` has saved a configuration (a real model may be ready)."""
    return global_config_path().exists()


def prepare(name: str, dest: Path, *, failing: bool = False) -> Path:
    """Copy a demo project into `dest/<name>` as a git repository with one commit.

    `failing` adds a test that shows the shop project's bug (check tour, change 1)."""
    project = dest / name
    shutil.copytree(
        TOUR_DIR / name,
        project,
        ignore=shutil.ignore_patterns("script.yaml", "__pycache__", ".pytest_cache"),
    )
    (project / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n.qed/\n", encoding="utf-8")
    if failing:
        _edit(project, SHOP_TEST, lambda t: t + FAILING_TEST)
    for args in (("init", "-q"), ("add", "-A"), ("commit", "-q", "-m", "demo project")):
        result = git(project, *args, identity=True)
        if not result.ok:
            detail = (result.error or result.output)[:200] or "no output"
            raise TourError(
                "The demo needs git, and git failed while setting up the demo project "
                f"({detail}). Check that `git --version` works, then run `qed try` again."
            )
    # The demo's tests run with QEDCode's own interpreter (pytest ships with QEDCode), so
    # nothing is detected, asked or downloaded.
    save_project(project, ProjectConfig(python=sys.executable, confirmed=True))
    return project


def make_dest() -> Path:
    """A fresh temporary folder for the demo (the CLI removes it unless --keep)."""
    return Path(tempfile.mkdtemp(prefix="qedcode-try-"))


def run_tour(
    name: str,
    dest: Path,
    *,
    client: ModelClient | None,
    cb: Callbacks,
    scripted: bool,
) -> TourResult:
    """Copy the project, solve its task, then re-run the receipt with `verify`."""
    try:
        project = prepare(name, dest)
    except (OSError, GitError) as exc:
        raise TourError(
            f"Could not copy the demo project into {dest} ({exc}). Check the free disk space "
            "and that the temporary folder is writable, then run `qed try` again."
        ) from exc
    task = task_text(name)
    model = scripted_model(name) if scripted or client is None else client
    result = api.solve(project, task, speed=Speed.NORMAL, cb=cb, client=model)
    tour = TourResult(project=project, task=task, scripted=scripted or client is None, solve=result)
    if result.receipt_path:
        tour.verify = api.verify(result.receipt_path, root=project)
    return tour


# ------------------------------------------------------------- the check tour (AGENTS.md 15.16)

SHOP_TEST = "tests/test_pages.py"
SHOP_CODE = "src/shop/pages.py"
BUG, FIX = "end = start + size - 1", "end = start + size"
FAILING_TEST = """

def test_a_full_page_has_every_item():
    assert paginate([1, 2, 3, 4], 1, 2) == [1, 2]
"""
PROOF_TEST = """from shop.pages import paginate


def test_a_full_page_has_every_item():
    assert paginate([1, 2, 3, 4], 1, 2) == [1, 2]


def test_the_last_page_keeps_its_last_item():
    assert paginate([1, 2, 3, 4, 5], 3, 2) == [5]
"""


@dataclass
class CheckStep:
    """One agent change of the check tour and what `qed check` said about it."""

    number: int
    outcome: CheckOutcome
    verify: VerifyResult | None = None

    @property
    def label(self) -> str:
        if self.outcome.tampered:
            return "Caught"
        grade = self.outcome.grade
        return {"A": "Proven", "B": "Unproven", "C": "Untested", "D": "Not checked"}.get(
            grade.value if grade else "", "Rejected"
        )

    @property
    def right(self) -> bool:
        """Did the demo show what it promises? (Otherwise the exit code says so.)"""
        return self.label == ("Caught", "Unproven", "Proven")[self.number - 1]

    def why(self) -> str:
        if self.outcome.tampered:
            return "skipped the failing test instead of fixing the code"
        receipt = self.outcome.receipt_path
        loaded = load_receipt(receipt) if receipt else None
        if loaded is None:
            return self.outcome.message
        passed, total = test_counts(loaded.checks)
        if self.label == "Proven":
            strength = loaded.mutation
            extra = (
                f" · proof strength {strength.killed}/{strength.total}"
                if strength is not None and strength.total
                else ""
            )
            return f"a new test failed before, passes now · {passed}/{total} pass{extra}"
        return f"{passed}/{total} tests pass, but none of them failed before the change"


def _edit(project: Path, rel: str, change: Callable[[str], str]) -> None:
    path = project / rel
    path.write_text(change(path.read_text(encoding="utf-8")), encoding="utf-8", newline="")


def run_check_tour(
    dest: Path, cb: Callbacks, on_step: Callable[[CheckStep], None]
) -> list[CheckStep]:
    """Grade three changes to the bundled shop project, as an agent might hand them in."""
    steps = []
    for number in (1, 2, 3):
        try:
            project = prepare(DEFAULT_PROJECT, dest / str(number), failing=number == 1)
        except (OSError, GitError) as exc:
            raise TourError(
                f"Could not copy the demo project into {dest} ({exc}). Check the free disk space "
                "and that the temporary folder is writable, then run `qed try` again."
            ) from exc
        if number == 1:
            _edit(
                project,
                SHOP_TEST,
                lambda t: (
                    "import pytest\n"
                    + t.replace("def test_a_full", "@pytest.mark.skip\ndef test_a_full")
                ),
            )
            _edit(project, SHOP_CODE, lambda t: t.replace("1-based page", "page"))
        else:
            _edit(project, SHOP_CODE, lambda t: t.replace(BUG, FIX))
        if number == 3:
            (project / "tests" / "test_full_pages.py").write_text(PROOF_TEST, encoding="utf-8")
        step = CheckStep(number, api.check(project, cb=cb))
        if number == 3 and step.outcome.receipt_path:
            step.verify = api.verify(step.outcome.receipt_path, root=project)
        steps.append(step)
        on_step(step)
    return steps


def next_steps() -> list[tuple[str, str]]:
    """What to do after the demo: (command, why)."""
    return [
        ("qed check", "in your own repository: grade what you or your agent changed (no model)"),
        ("qed setup", "pick a local or cloud model so QEDCode can write proven fixes for you"),
        ("qed connect", "let Claude Code, Cursor, OpenCode and others call QEDCode as a tool"),
    ]
