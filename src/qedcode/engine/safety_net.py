"""Safety Net Builder (`qed tests`, AGENTS.md section 15.11).

Find public functions no test mentions, have the model write tests that pass on today's code, and
keep only the test functions that catch at least one mutant of the function they test. The kept
tests come back as a normal graded result with a receipt, ready for `qed apply`.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from qedcode.engine.budget import settings_for
from qedcode.engine.check import project_config_for
from qedcode.engine.lock import ProjectBusy, project_lock
from qedcode.engine.roles import Roles
from qedcode.engine.solve import write_last_result
from qedcode.models.ids import new_task_id
from qedcode.models.schemas import (
    Callbacks,
    Candidate,
    Event,
    MutationScore,
    SolveResult,
    Speed,
    TaskKind,
    TaskSpec,
)
from qedcode.project.context import ContextFile, read_project_file
from qedcode.project.detect import find_test_files
from qedcode.project.paths import is_test_file
from qedcode.project.repo_map import list_source_files
from qedcode.runtime.client import ModelClient
from qedcode.vcs import worktree
from qedcode.vcs.git import git, git_out
from qedcode.vcs.snapshot import snapshot, snapshot_ref
from qedcode.verify.baseline import baseline_run
from qedcode.verify.cascade import Cancelled, CascadeContext, evaluate_candidate
from qedcode.verify.mutate import mutants_for
from qedcode.verify.receipt import build_receipt, save_receipt, summary_line
from qedcode.verify.runners import run_tests

TARGETS = {Speed.FAST: 1, Speed.NORMAL: 3, Speed.SURE: 6}
REQUEST = "Build a safety net: tests for code no test covers"


@dataclass
class Target:
    path: str
    symbol: str  # "func" or "Class.method"
    start: int
    end: int

    @property
    def name(self) -> str:
        return self.symbol.rsplit(".", 1)[-1]


@dataclass
class NetFile:
    target: Target
    path: str
    code: str
    kept: list[str] = field(default_factory=list)
    dropped_failing: list[str] = field(default_factory=list)
    dropped_padding: list[str] = field(default_factory=list)
    score: MutationScore = field(default_factory=MutationScore)


def _public_functions(tree: ast.Module) -> list[tuple[str, ast.AST]]:
    found: list[tuple[str, ast.AST]] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if not node.name.startswith("_"):
                found.append((node.name, node))
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            for item in node.body:
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                    if not item.name.startswith("_"):
                        found.append((f"{node.name}.{item.name}", item))
    return found


def find_untested(root: Path, path: str = "", limit: int = 3) -> list[Target]:
    """Public functions in non-test Python files whose name no test file mentions, largest first."""
    return public_targets(root, path, limit, untested_only=True)


def public_targets(
    root: Path, path: str = "", limit: int = 3, *, untested_only: bool = False
) -> list[Target]:
    """Public functions and methods in non-test Python files, largest first (optionally only
    those no test file mentions by name)."""
    tests_text = (
        "\n".join(read_project_file(root, t) or "" for t in find_test_files(root, "python"))
        if untested_only
        else ""
    )
    wanted = path.strip("/").replace("\\", "/")
    targets: list[Target] = []
    for rel in list_source_files(root, "python"):
        if is_test_file(rel) or (wanted and not (rel == wanted or rel.startswith(wanted + "/"))):
            continue
        text = read_project_file(root, rel)
        try:
            tree = ast.parse(text or "")
        except SyntaxError:
            continue
        for symbol, node in _public_functions(tree):
            name = symbol.rsplit(".", 1)[-1]
            if untested_only and re.search(rf"\b{re.escape(name)}\b", tests_text):
                continue
            end = getattr(node, "end_lineno", node.lineno) or node.lineno
            targets.append(Target(rel, symbol, node.lineno, end))
    targets.sort(key=lambda t: (-(t.end - t.start), t.path, t.start))
    return targets[:limit]


def net_test_path(target: Target) -> str:
    module = Path(target.path).stem
    slug = re.sub(r"[^a-z0-9]+", "_", f"{module}_{target.name}".lower()).strip("_")
    return f"tests/test_qed_net_{slug}.py"


def _test_name(key: str) -> str:
    """`tests.test_x::TestA::test_b[1]` -> `test_b`."""
    return key.split("::")[-1].split("[", 1)[0]


def keep_only(code: str, names: set[str]) -> str:
    """The test file with every top-level test function not in `names` removed."""
    tree = ast.parse(code)
    tree.body = [
        node
        for node in tree.body
        if not (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name.startswith("test")
            and node.name not in names
        )
    ]
    return ast.unparse(tree) + "\n"


class SafetyNet:
    """One `qed tests` run: targets, model replies, filtering in a worktree, the receipt."""

    def __init__(self, root: Path, speed: Speed, cb: Callbacks, client: ModelClient) -> None:
        self.root, self.speed, self.cb, self.client = root, speed, cb, client
        self.spec = TaskSpec(id=new_task_id(), request=REQUEST, kind=TaskKind.TEST, speed=speed)
        self.notes: list[str] = []

    def emit(self, kind: str, message: str) -> None:
        self.cb.on_event(Event(kind=kind, message=message))  # type: ignore[arg-type]

    def run(self, path: str) -> SolveResult:
        self.emit("stage", "Taking a snapshot of your project...")
        self.snap = snapshot(self.root, self.spec.id)
        self.project = project_config_for(self.root, self.notes)
        self.run_dir = self.root / ".qed" / "runs" / self.spec.id
        if self.project.language != "python":
            return self._end("error", "The Safety Net Builder works on Python projects for now.")
        targets = find_untested(self.root, path, TARGETS[self.speed])
        if not targets:
            return self._end("no_fix", "Every public function is already mentioned by a test.")
        self.emit("stage", "Untested: " + ", ".join(f"{t.path}:{t.symbol}" for t in targets))
        roles = Roles(
            self.client,
            self.run_dir,
            on_event=self.cb.on_event,
            cancelled=self.cb.cancelled,
            project=self.project,
        )
        example = self._example_test()
        wt = worktree.create(self.root, self.snap, f"{self.spec.id}-net")
        try:
            files = [f for t in targets if (f := self._one(roles, t, example, wt)) is not None]
            if not files:
                return self._end("no_fix", "No written test caught a mutant, so none were kept.")
            commit = self._commit(wt, files)
        finally:
            worktree.remove(self.root, wt)
        return self._grade(commit, files)

    def _example_test(self) -> ContextFile | None:
        tests = find_test_files(self.root, "python")
        text = read_project_file(self.root, tests[0]) if tests else None
        return ContextFile(tests[0], text) if tests and text is not None else None

    def _one(self, roles: Roles, target: Target, example, wt: Path) -> NetFile | None:
        if self.cb.cancelled():
            raise Cancelled
        test_path = net_test_path(target)
        source = read_project_file(self.root, target.path) or ""
        self.emit("stage", f"Writing tests for {target.symbol}...")
        written = roles.safety_net(
            target.symbol, target.path, [ContextFile(target.path, source)], example, test_path
        )
        if written is None:
            self.notes.append(f"No usable tests came back for {target.symbol}.")
            return None
        net = NetFile(target, test_path, written.content)
        return net if self._filter(net, wt, source) else None

    def _run(self, wt: Path, net: NetFile) -> dict[str, str] | None:
        (wt / net.path).parent.mkdir(parents=True, exist_ok=True)
        (wt / net.path).write_text(net.code, encoding="utf-8")
        timeout = float(self.project.test_timeout_seconds)
        out = run_tests(
            wt, self.project, self.root, [net.path], timeout, cancelled=self.cb.cancelled
        )
        if out.status in ("collection_error", "error", "timeout", "cancelled"):
            return None
        return {_test_name(k): v for k, v in out.as_map().items()}

    def _filter(self, net: NetFile, wt: Path, source: str) -> bool:
        """Drop tests that fail today, then tests that catch no mutant. True if any are kept."""
        today = self._run(wt, net)
        if not today:
            self.notes.append(f"The tests for {net.target.symbol} did not run; none were kept.")
            return False
        passing = {n for n, outcome in today.items() if outcome == "passed"}
        net.dropped_failing = sorted(set(today) - passing)
        if not passing:
            return False
        net.code = keep_only(net.code, passing)
        lines = set(range(net.target.start, net.target.end + 1))
        mutants = mutants_for(net.target.path, source, lines)
        caught: set[str] = set()
        target_file = wt / net.target.path
        for mutant in mutants:
            target_file.write_text(mutant.source, encoding="utf-8")
            try:
                outcomes = self._run(wt, net)
            finally:
                target_file.write_text(source, encoding="utf-8")
            failed = {n for n, o in (outcomes or {}).items() if o != "passed"}
            if outcomes is None or failed:
                net.score.killed += 1
                caught |= failed if outcomes is not None else passing
            else:
                net.score.survivors.append(f"{net.target.path} {mutant.description}")
        net.score.total = len(mutants)
        net.kept = sorted(passing & caught)
        net.dropped_padding = sorted(passing - caught)
        if not net.kept:
            (wt / net.path).unlink(missing_ok=True)
            return False
        net.code = keep_only(net.code, set(net.kept))
        (wt / net.path).write_text(net.code, encoding="utf-8")
        self.emit(
            "log",
            f"{net.target.symbol}: kept {len(net.kept)} test(s), caught "
            f"{net.score.killed}/{net.score.total} mutants"
            + (
                f", dropped {len(net.dropped_padding)} that caught none"
                if net.dropped_padding
                else ""
            ),
        )
        return True

    def _commit(self, wt: Path, files: list[NetFile]) -> str:
        git(wt, "add", "--", *[f.path for f in files])
        git(wt, "commit", "-q", "-m", "QEDCode safety net", identity=True)
        return git_out(wt, "rev-parse", "HEAD")

    def _grade(self, commit: str, files: list[NetFile]) -> SolveResult:
        settings = settings_for(self.speed)
        base = baseline_run(
            self.root,
            self.snap,
            self.project,
            full=False,
            related=[],
            run_dir=self.run_dir,
            name=f"{self.spec.id}-base",
            cancelled=self.cb.cancelled,
        )
        ctx = CascadeContext(
            root=self.root,
            base_commit=self.snap,
            baseline=base.baseline,
            project=self.project,
            task=self.spec,
            run_dir=self.run_dir,
            on_event=self.cb.on_event,
            confirm=self.cb.confirm,
            cancelled=self.cb.cancelled,
        )
        cand = Candidate(id="n1", role="user", model=self.client.name)
        evaluate_candidate(ctx, cand, start_commit=commit, run_full=settings.full_on_finalist)
        if cand.grade is None:
            return self._end("no_fix", f"The kept tests failed the {cand.failed_stage} check.")
        receipt = build_receipt(
            root=self.root,
            task=self.spec,
            cand=cand,
            baseline=base.baseline,
            project=self.project,
            base_commit=self.snap,
            base_ref=snapshot_ref(self.spec.id),
            model_info={"model": self.client.name, "mode": "safety-net"},
        )
        receipt.mutation = MutationScore(
            killed=sum(f.score.killed for f in files),
            total=sum(f.score.total for f in files),
            survivors=[s for f in files for s in f.score.survivors],
        )
        path = save_receipt(self.root, receipt)
        write_last_result(self.root, self.spec.id, cand.id, str(path))
        kept = sum(len(f.kept) for f in files)
        line = summary_line(receipt)
        self.emit("result", line)
        message = (
            f"Kept {kept} test(s) in {len(files)} file(s); they catch {receipt.mutation.killed} of "
            f"{receipt.mutation.total} mutants. A change that only adds tests is graded C by "
            f"design (it changes no behaviour). Review them, then `qed apply {self.spec.id}`."
        )
        return SolveResult(
            status="done",
            task=self.spec,
            best=cand,
            candidates=[cand],
            receipt_path=str(path),
            message=message,
            explanation=line,
        )

    def _end(self, status: str, message: str) -> SolveResult:
        text = " ".join([message, *self.notes]).strip()
        return SolveResult(status=status, task=self.spec, message=text)  # type: ignore[arg-type]


def build_safety_net(
    root: Path, path: str, speed: Speed, cb: Callbacks, client: ModelClient
) -> SolveResult:
    """Run the Safety Net Builder under the project lock."""
    net = SafetyNet(root, speed, cb, client)
    try:
        with project_lock(root, net.spec.id):
            return net.run(path)
    except ProjectBusy as exc:
        return SolveResult(status="error", task=net.spec, message=str(exc))
    except Cancelled:
        return SolveResult(status="cancelled", task=net.spec, message="Cancelled.")
