"""Shared data models (AGENTS.md section 5). Paths inside models are repo-relative with `/`."""

from __future__ import annotations

from enum import Enum
from typing import Literal, Protocol

from pydantic import BaseModel, Field


class Speed(str, Enum):
    FAST = "fast"
    NORMAL = "normal"
    SURE = "sure"


class Mode(str, Enum):
    LOCAL = "local"
    MIX = "mix"
    CLOUD = "cloud"


class Grade(str, Enum):  # rank: A=4 > B=3 > C=2 > D=1
    A = "A"
    B = "B"
    C = "C"
    D = "D"

    def rank(self) -> int:
        """Return a number where a higher grade ranks higher (A=4 ... D=1)."""
        return _GRADE_RANKS[self.value]


_GRADE_RANKS = {"A": 4, "B": 3, "C": 2, "D": 1}


class TaskKind(str, Enum):
    BUGFIX = "bugfix"
    FEATURE = "feature"
    REFACTOR = "refactor"
    TEST = "test"
    DOCS = "docs"
    OTHER = "other"


Stage = Literal["apply", "parse", "scope", "risk", "lint", "fbpa", "targeted", "full"]
STAGE_ORDER: list[str] = ["apply", "parse", "scope", "risk", "lint", "fbpa", "targeted", "full"]


class TaskSpec(BaseModel):
    id: str  # "t-20261120-153201-ab12"
    request: str  # the user's words, verbatim
    kind: TaskKind = TaskKind.OTHER
    acceptance: list[str] = Field(default_factory=list)  # max 4 checkable lines
    speed: Speed = Speed.NORMAL
    target_grade: Grade = Grade.B
    allow_test_edits: bool = False


class EditOp(BaseModel):
    op: Literal[
        "replace_symbol", "insert_after_symbol", "add_import", "search_replace", "create_file"
    ]
    path: str
    symbol: str | None = None  # "func" or "Class.method"
    search: str | None = None  # search_replace only
    code: str  # new code or replacement text


class ParsedReply(BaseModel):
    plan: list[str] = Field(default_factory=list)
    edits: list[EditOp] = Field(default_factory=list)
    extra: dict[str, str] = Field(default_factory=dict)  # e.g. FAILS NOW BECAUSE
    errors: list[str] = Field(default_factory=list)  # format problems


class TestResult(BaseModel):
    __test__ = False  # not a pytest test class, despite the name

    key: str  # "<classname>::<name>" from JUnit XML
    outcome: Literal["passed", "failed", "error", "skipped"]
    message: str = ""  # first failure line, trimmed


class CheckResult(BaseModel):
    stage: Stage
    passed: bool
    command: list[str] | None = None
    exit_code: int | None = None
    duration_s: float = 0.0
    summary: str = ""  # one line for humans, <= 300 chars
    details: str = ""  # trimmed text for the repairer, <= 4000 chars
    tests: list[TestResult] = Field(default_factory=list)
    log_path: str | None = None
    needs_approval: bool = False  # risk stage only


class Candidate(BaseModel):
    id: str  # "c1", "c2", "r1" (repair), "h1" (helper)
    role: Literal["coder", "repairer", "helper", "user"]  # "user" = qed check
    parent_id: str | None = None
    model: str = ""
    temperature: float = 0.0
    reply: ParsedReply = Field(default_factory=ParsedReply)
    worktree: str | None = None
    commit: str | None = None
    diff: str = ""
    diff_lines: int = 0
    checks: list[CheckResult] = Field(default_factory=list)
    failed_stage: Stage | None = None
    grade: Grade | None = None  # None = rejected
    tests_passed: int = 0
    full_suite_ran: bool = False
    code_changed: bool = True  # False = outside tests, only comments or formatting changed


class Baseline(BaseModel):
    commit: str
    results: dict[str, str] = Field(default_factory=dict)  # key -> outcome
    full: bool = False
    runnable: bool = True  # False = pytest could not run at all
    reason: str = ""


class Receipt(BaseModel):
    schema_version: Literal[1] = 1
    qed_version: str
    created_at: str  # ISO 8601, UTC
    project_name: str
    task: TaskSpec
    base_commit: str  # snapshot or HEAD commit sha
    base_ref: str  # "refs/qed/snapshots/<task id>" or "HEAD"
    patch: str  # git diff --binary base..candidate
    patch_sha256: str
    tests_added: list[str] = Field(default_factory=list)
    fbpa_tests: list[str] = Field(default_factory=list)
    known_failures: list[str] = Field(default_factory=list)
    checks: list[CheckResult]
    protected_changes: list[str] = Field(default_factory=list)
    grade: Grade
    unverified: list[str] = Field(default_factory=list)  # plain-English gaps
    model_info: dict = Field(default_factory=dict)  # backend, model, ctx, temps, helper_used
    commands: list[list[str]] = Field(default_factory=list)  # what verify re-runs
    sandbox: str = ""  # "docker" when the tests ran in the sealed test box, else ""


class SolveResult(BaseModel):
    status: Literal["done", "no_fix", "needs_input", "cancelled", "error"]
    task: TaskSpec
    best: Candidate | None = None
    candidates: list[Candidate] = Field(default_factory=list)
    receipt_path: str | None = None
    explanation: str = ""
    question: str | None = None
    message: str = ""  # one-line human summary


class Event(BaseModel):
    kind: Literal["stage", "candidate", "check", "model", "log", "warning", "result"]
    message: str  # ready to show to a human
    data: dict = Field(default_factory=dict)


class Callbacks(Protocol):  # implemented by each UI
    def on_event(self, event: Event) -> None: ...
    def ask(self, question: str) -> str | None: ...  # None = cannot ask (non-interactive)
    def confirm(self, prompt: str) -> bool: ...  # False when non-interactive
    def cancelled(self) -> bool: ...
