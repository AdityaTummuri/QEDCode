"""The controller (AGENTS.md section 6): cheap things first, stop the moment the target is reached.

snapshot -> intake -> files -> baseline -> repro test -> k tries -> repairs -> helper (mix)
-> best candidate -> full suite on the finalist -> receipt -> explanation.

This module only decides what to do next. The model calls live in roles.py, the checks in
verify/, the git work in vcs/ and the ranking in select.py.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from qedcode.config import ProjectConfig
from qedcode.engine import select
from qedcode.engine.budget import (
    SpeedSettings,
    settings_for,
    target_grade,
    temperatures_for,
    test_writer_on,
)
from qedcode.engine.check import project_config_for
from qedcode.engine.lock import ProjectBusy, project_lock
from qedcode.engine.roles import Roles
from qedcode.models.ids import new_task_id
from qedcode.models.schemas import (
    Callbacks,
    Candidate,
    Event,
    Mode,
    SolveResult,
    Speed,
    TaskSpec,
)
from qedcode.project.context import (
    ContextBundle,
    build_context,
    files_at_commit,
    repro_test_path,
)
from qedcode.project.detect import find_test_files
from qedcode.project.memory import Memory, example_budget, render_examples
from qedcode.runtime.client import ModelClient, ModelError
from qedcode.vcs import worktree
from qedcode.vcs.git import GitError, git
from qedcode.vcs.snapshot import CANDIDATE_REF, keep_candidate, snapshot, snapshot_ref
from qedcode.verify.baseline import BaselineRun, baseline_run, fails_on_base
from qedcode.verify.cascade import (
    Cancelled,
    CascadeContext,
    ReproTest,
    evaluate_candidate,
    run_full,
)
from qedcode.verify.guard import changed_paths
from qedcode.verify.mutate import strength_for
from qedcode.verify.receipt import build_receipt, save_receipt, summary_line

LAST_RESULT = "last_result.json"
DEFAULT_QUESTION = "Can you give an example of the input and the output you expect?"


@dataclass
class _Run:
    """The state of one solve call."""

    root: Path
    request: str
    speed: Speed
    mode: Mode
    cb: Callbacks
    client: ModelClient
    helper_factory: Callable[[], ModelClient] | None
    helper_preapproved: bool
    project: ProjectConfig | None
    allow_risky: bool
    allow_test_edits: bool
    settings: SpeedSettings
    resume: SolveResult | None
    learn: bool = False
    examples: str = ""
    pause_s: float = 0.0  # quiet mode: wait between model calls
    sleep: Callable[[float], None] = time.sleep
    given_repro: tuple[str, str] | None = None  # (path, content): a test that must fail first
    calls_made: int = 0
    spec: TaskSpec = field(init=False)
    notes: list[str] = field(default_factory=list)
    pool: list[Candidate] = field(default_factory=list)
    repaired: set[str] = field(default_factory=set)
    question: str | None = None
    helper_name: str = ""
    helper_calls: int = 0
    snap: str = ""
    run_dir: Path = field(init=False)
    roles: Roles = field(init=False)
    ctx: ContextBundle = field(init=False)
    base_run: BaselineRun = field(init=False)
    repro: ReproTest | None = None
    cctx: CascadeContext = field(init=False)
    deadline: float = 0.0

    # ------------------------------------------------------------------ small helpers

    def emit(self, kind: str, message: str, **data) -> None:
        self.cb.on_event(Event(kind=kind, message=message, data=data))  # type: ignore[arg-type]

    def out_of_time(self) -> bool:
        return time.monotonic() >= self.deadline

    def should_stop(self) -> bool:
        return self.cb.cancelled() or self.out_of_time()

    def check_cancel(self) -> None:
        if self.cb.cancelled():
            raise Cancelled

    def cooldown(self) -> None:
        """Quiet mode: let the computer cool down before the next model call (never the first)."""
        first = self.calls_made == 0
        self.calls_made += 1
        if first or self.pause_s <= 0:
            return
        self.emit("log", f"Quiet mode: pausing {self.pause_s:g} seconds to keep the computer cool.")
        waited = 0.0
        while waited < self.pause_s and not self.cb.cancelled():
            step = min(0.1, self.pause_s - waited)
            self.sleep(step)
            waited += step

    def done_trying(self) -> bool:
        return any(
            select.reached(c, self.spec.target_grade) or select.promising(c, self.spec.target_grade)
            for c in self.pool
        )

    # ------------------------------------------------------------------ the steps

    def execute(self) -> SolveResult:
        self.deadline = time.monotonic() + self.settings.time_budget_s
        self.emit("stage", "Taking a snapshot of your project...")
        worktree.prune(self.root)
        cleanup_old_candidates(self.root)
        prune_old_runs(self.root)
        self.snap = snapshot(self.root, self.spec.id)
        self.project = self.project or project_config_for(self.root, self.notes)
        self.run_dir = self.root / ".qed" / "runs" / self.spec.id
        self.roles = Roles(
            self.client,
            self.run_dir,
            on_event=self.cb.on_event,
            cancelled=self.cb.cancelled,
            project=self.project,
        )
        self.understand()
        self.find_files()
        self.run_baseline()
        self.make_repro()
        self.spec.target_grade = target_grade(self.speed, self.repro is not None)
        self.cctx = CascadeContext(
            root=self.root,
            base_commit=self.snap,
            baseline=self.base_run.baseline,
            project=self.project,
            task=self.spec,
            run_dir=self.run_dir,
            baseline_full_seconds=self.base_run.full_seconds,
            repro=self.repro,
            on_event=self.cb.on_event,
            confirm=self.cb.confirm,
            cancelled=self.cb.cancelled,
            allow_risky=self.allow_risky,
        )
        self.add_resumed()
        self.try_coders()
        self.repair_near_misses()
        self.ask_helper()
        return self.finalize()

    def understand(self) -> None:
        self.check_cancel()
        result = self.roles.intake(self.request, self.speed)
        if result.question:
            answer = self.cb.ask(result.question)
            if answer:
                result = self.roles.intake(f"{self.request}\n{answer}", self.speed)
            else:
                self.question = result.question
                self.emit(
                    "warning", f"The task may be unclear: {result.question} Continuing anyway."
                )
        self.spec.kind = result.kind
        self.spec.acceptance = result.acceptance

    def find_files(self) -> None:
        self.check_cancel()
        self.ctx = build_context(
            self.root,
            self.spec,
            lambda repo_map, hits: self.roles.locator(
                self.spec, repo_map, hits, self.settings.max_files
            ),
            max_files=self.settings.max_files,
            context_tokens=self.client.context_tokens,
            on_event=self.cb.on_event,
            language=self.project.language,  # type: ignore[union-attr]
        )
        for note in self.ctx.notes:
            self.emit("log", note)
        self.load_examples()

    def load_examples(self) -> None:
        """Similar earlier fixes of this project, for the local model only (AGENTS.md 15.1)."""
        if not self.learn or self.mode is Mode.CLOUD:
            return
        budget = example_budget(self.client.context_tokens)
        if budget <= 0:
            return
        lessons = Memory(self.root).similar(self.request)
        self.examples = render_examples(lessons, budget)
        if self.examples:
            self.emit("log", f"Using {len(lessons)} earlier fix(es) from this project as examples.")

    def run_baseline(self) -> None:
        self.check_cancel()
        what = "all your tests" if self.settings.baseline_full else "the related tests"
        self.emit("stage", f"Running {what} to see what already passes...")
        self.base_run = baseline_run(
            self.root,
            self.snap,
            self.project,  # type: ignore[arg-type]
            full=self.settings.baseline_full,
            related=self.ctx.related_tests,
            run_dir=self.run_dir,
            name=f"{self.spec.id}-baseline",
            cancelled=self.cb.cancelled,
            full_limit_s=self.settings.full_suite_limit_s or None,
        )
        base = self.base_run.baseline
        if base.full_skipped:
            self.emit(
                "warning",
                f"Your full test suite is too slow to run on every task ({base.full_skipped}), "
                "so only the related tests run; the best grade possible is B.",
            )
        if base.slow_tests:
            self.emit(
                "warning",
                "These related test files are too slow to run on every try and are left out: "
                + ", ".join(base.slow_tests[:5])
                + (" ..." if len(base.slow_tests) > 5 else ""),
            )
        if not self.base_run.baseline.runnable:
            self.emit(
                "warning",
                f"Tests could not run before the change ({self.base_run.baseline.reason}); "
                "the best grade possible is C.",
            )

    def make_repro(self) -> None:
        if self.given_repro is not None and self.base_run.baseline.runnable:
            self.use_given_repro(*self.given_repro)
            return
        if not (test_writer_on(self.speed, self.spec.kind) and self.base_run.baseline.runnable):
            return
        language = self.project.language  # type: ignore[union-attr]
        path = repro_test_path(
            self.request, find_test_files(self.root, language), self.root, language
        )
        for attempt in (1, 2):
            self.check_cancel()
            self.emit("stage", "Writing a test that shows the problem...")
            written = self.roles.test_writer(
                self.spec, self.ctx.files, self.ctx.example_test, path, label=f"a{attempt}"
            )
            if written is None:
                continue
            failures = fails_on_base(
                self.root,
                self.snap,
                self.project,  # type: ignore[arg-type]
                written.path,
                written.content,
                run_dir=self.run_dir,
                name=f"{self.spec.id}-repro",
                cancelled=self.cb.cancelled,
            )
            if failures:
                self.repro = ReproTest(written.path, written.content, failures)
                self.emit("stage", "Writing a test that shows the problem ... done (it fails now)")
                return
            self.emit(
                "warning",
                "The new test passes on the current code, so it proves nothing; "
                + ("trying once more." if attempt == 1 else "continuing without it."),
            )

    def use_given_repro(self, path: str, content: str) -> None:
        """A failing test supplied by the caller (Bug Hunter, Smart Questions) is the repro."""
        failures = fails_on_base(
            self.root,
            self.snap,
            self.project,  # type: ignore[arg-type]
            path,
            content,
            run_dir=self.run_dir,
            name=f"{self.spec.id}-repro",
            cancelled=self.cb.cancelled,
        )
        if failures:
            self.repro = ReproTest(path, content, failures)
            self.emit("stage", f"Using the test that shows the problem ({path}): it fails now")
        else:
            self.emit("warning", f"The given test {path} passes on the current code; ignored.")

    def add_resumed(self) -> None:
        """'Try harder': begin with the previous best candidate if the files are unchanged."""
        old = self.resume
        if old is None:
            return
        previous = old.best or select.near_miss(old.candidates)
        if previous is None or previous.commit is None:
            return
        old_snap = git(self.root, "rev-parse", f"{snapshot_ref(old.task.id)}^{{tree}}")
        new_snap = git(self.root, "rev-parse", f"{self.snap}^{{tree}}")
        if not (old_snap.ok and new_snap.ok and old_snap.stdout == new_snap.stdout):
            self.emit("log", "Your files changed since the last try, so it starts from scratch.")
            return
        carried = previous.model_copy(deep=True)
        carried.id = f"p{len([c for c in self.pool if c.id.startswith('p')]) + 1}"
        carried.worktree = None
        keep_candidate(self.root, self.spec.id, carried.id, carried.commit or "")
        self.pool.append(carried)
        self.emit("log", f"Starting from the earlier attempt {previous.id}.")

    def evaluate(self, cand: Candidate, start_commit: str | None = None) -> Candidate:
        """Check a candidate. One that looks good also gets the full suite right away, so that
        stopping early means the proof is complete (and a regression sends the loop on)."""
        evaluate_candidate(self.cctx, cand, start_commit=start_commit, run_full=False)
        self.pool.append(cand)
        if (
            select.promising(cand, self.spec.target_grade)
            and self.settings.full_on_finalist
            and self.base_run.baseline.runnable
            and not self.base_run.baseline.full_skipped
            and not cand.full_suite_ran
        ):
            self.check_cancel()
            self.emit("stage", f"Running all tests on {cand.id}...")
            run_full(self.cctx, cand)
            if cand.grade is None:
                self.emit("warning", f"{cand.id} broke other tests, so it is rejected.")
        return cand

    def try_coders(self) -> None:
        repro_text = self.repro.content if self.repro else None
        for number, temperature in enumerate(temperatures_for(self.speed), start=1):
            if self.should_stop() or self.done_trying():
                break
            self.cooldown()
            self.emit("stage", f"Try {number}: asking the model (temperature {temperature})...")
            reply = self.roles.coder(
                self.spec,
                self.ctx.files,
                repro_text,
                temperature,
                label=f"c{number}",
                examples=self.examples,
            )
            cand = Candidate(
                id=f"c{number}",
                role="coder",
                model=self.client.name,
                temperature=temperature,
                reply=reply.reply,
            )
            self.evaluate(cand)

    def repair_near_misses(self) -> None:
        for number in range(1, self.settings.repair_rounds + 1):
            if self.should_stop() or self.done_trying():
                break
            near = select.near_miss([c for c in self.pool if c.id not in self.repaired])
            if near is None:
                break
            self.repaired.add(near.id)
            self.cooldown()
            self.emit("stage", f"Repairing {near.id} (it failed at {near.failed_stage})...")
            files = (
                files_at_commit(self.root, near.commit, changed_paths(near.diff))
                if near.commit and near.diff
                else self.ctx.files
            )
            reply = self.roles.repairer(
                self.spec, near, failure_text(near), files or self.ctx.files, label=f"r{number}"
            )
            repair = Candidate(
                id=f"r{number}",
                role="repairer",
                parent_id=near.id,
                model=self.client.name,
                temperature=0.3,
                reply=reply.reply,
            )
            self.evaluate(repair, start_commit=near.commit)
            if select.no_progress(repair, near):
                self.emit("log", "The repair got nowhere, so repairing stops.")
                break

    def ask_helper(self) -> None:
        if (
            self.mode is not Mode.MIX
            or self.settings.helper == "never"
            or self.helper_factory is None
            or self.done_trying()
            or self.should_stop()
        ):
            return
        if not self.helper_preapproved and not self.cb.confirm(
            "The local model is stuck. Ask the helper model? Parts of your code will be sent to it."
        ):
            return
        try:
            helper = self.helper_factory()
        except ModelError as exc:
            self.emit("warning", f"The helper model is not available: {exc}")
            return
        self.emit("stage", f"Asking the helper model ({helper.name})...")
        helper_roles = Roles(
            helper,
            self.run_dir,
            on_event=self.cb.on_event,
            cancelled=self.cb.cancelled,
            project=self.project,
        )
        compact = self.ctx.compact()
        reply = helper_roles.coder(
            self.spec,
            compact.files,
            self.repro.content if self.repro else None,
            0.2,
            label="h1",
        )
        self.helper_calls = helper_roles.calls
        self.helper_name = helper.name
        cand = Candidate(
            id="h1", role="helper", model=helper.name, temperature=0.2, reply=reply.reply
        )
        self.evaluate(cand)

    # ------------------------------------------------------------------ the end

    def pick_finalist(self) -> Candidate | None:
        """The best candidate, after the full suite has been run on it where it is required."""
        while True:
            best = select.best(self.pool)
            if best is None:
                return None
            if (
                not self.settings.full_on_finalist
                or best.full_suite_ran
                or not self.base_run.baseline.runnable
                or self.base_run.baseline.full_skipped
            ):
                return best
            self.check_cancel()
            self.emit("stage", f"Running all tests on {best.id}, the best candidate...")
            run_full(self.cctx, best)
            if best.grade is not None:
                return best
            self.emit("warning", f"{best.id} broke the full test suite; looking at the next best.")

    def finalize(self) -> SolveResult:
        self.check_cancel()  # a cancel that arrived between steps still means "cancelled"
        chosen = self.pick_finalist()
        if chosen is None:
            return self.no_fix()
        info = self.model_info()
        receipt = build_receipt(
            root=self.root,
            task=self.spec,
            cand=chosen,
            baseline=self.base_run.baseline,
            project=self.project,  # type: ignore[arg-type]
            base_commit=self.snap,
            base_ref=snapshot_ref(self.spec.id),
            model_info=info,
        )
        self.measure_strength(receipt)
        path = save_receipt(self.root, receipt)
        line = summary_line(receipt)
        self.emit("stage", "Explaining the change...")
        explanation = self.roles.explainer(self.spec, chosen.diff, line, changed_paths(chosen.diff))
        self.emit("result", line, grade=chosen.grade.value if chosen.grade else None)
        write_last_result(self.root, self.spec.id, chosen.id, str(path))
        return SolveResult(
            status="done",
            task=self.spec,
            best=chosen,
            candidates=self.pool,
            receipt_path=str(path),
            explanation=explanation,
            message=line,
        )

    def measure_strength(self, receipt) -> None:
        """Mutation testing of the new tests (AGENTS.md 15.9); it never changes the grade."""
        if self.speed is Speed.FAST or not receipt.fbpa_tests:
            return
        self.emit("stage", "Measuring proof strength (small breakages of the changed lines)...")
        score = strength_for(self.root, receipt, self.project, cancelled=self.cb.cancelled)  # type: ignore[arg-type]
        if score is not None and score.total:
            receipt.mutation = score
            self.emit(
                "log",
                f"Proof strength {score.killed}/{score.total}: the new tests caught "
                f"{score.killed} of {score.total} mutants.",
            )

    def no_fix(self) -> SolveResult:
        near = select.near_miss(self.pool)
        if near is not None:
            last = near.checks[-1].summary if near.checks else "no details"
            message = (
                f"No fix passed the checks. The closest attempt ({near.id}) failed at "
                f"{near.failed_stage}: {last}"
            )
        else:
            message = "No fix passed the checks, and no attempt got far enough to compare."
        status = "needs_input" if self.question else "no_fix"
        write_last_result(self.root, self.spec.id, near.id if near else "", "")
        return SolveResult(
            status=status,  # type: ignore[arg-type]
            task=self.spec,
            candidates=self.pool,
            message=message,
            question=self.question or DEFAULT_QUESTION,
        )

    def model_info(self) -> dict:
        used = [c.temperature for c in self.pool if c.role == "coder"]
        return {
            "backend": self.client.name.split(":", 1)[0],
            "model": self.client.name,
            "ctx": self.client.context_tokens,
            "mode": self.mode.value,
            "speed": self.speed.value,
            "temperatures": used,
            "model_calls": self.roles.calls + self.helper_calls,
            "helper_used": bool(self.helper_name),
            "helper": self.helper_name,
        }


# --------------------------------------------------------------------------- helpers


def failure_text(near: Candidate) -> str:
    """What went wrong with a candidate, in the words the repairer needs."""
    if not near.checks:
        return "The change could not be checked."
    last = near.checks[-1]
    head = f"The change failed at the {near.failed_stage} check: {last.summary}"
    return f"{head}\n{last.details}".strip()


def write_last_result(root: Path, task_id: str, best_id: str, receipt: str) -> None:
    path = root / ".qed" / LAST_RESULT
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"task_id": task_id, "best_id": best_id, "receipt_path": receipt}),
            encoding="utf-8",
        )
    except OSError:
        pass


def read_last_result(root: Path | str) -> dict | None:
    try:
        return json.loads((Path(root) / ".qed" / LAST_RESULT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def prune_old_runs(root: Path, keep: int = 20) -> int:
    """Delete the oldest `.qed/runs/<task id>/` folders, keeping the newest `keep`."""
    import shutil

    runs = root / ".qed" / "runs"
    if not runs.exists():
        return 0
    folders = sorted(p for p in runs.iterdir() if p.is_dir())  # ids start with a timestamp
    stale = folders[: max(len(folders) - keep, 0)]
    for folder in stale:
        shutil.rmtree(folder, ignore_errors=True)
    return len(stale)


def cleanup_old_candidates(root: Path) -> None:
    """Drop candidate refs of earlier tasks, keeping the last task's (its winner may be applied)."""
    last = read_last_result(root)
    keep = f"{CANDIDATE_REF}/{last['task_id']}/" if last else None
    listed = git(root, "for-each-ref", "--format=%(refname)", f"{CANDIDATE_REF}/")
    for ref in listed.stdout.split("\n"):
        if ref.strip() and not (keep and ref.startswith(keep)):
            git(root, "update-ref", "-d", ref)


def solve(
    root: Path | str,
    request: str,
    speed: Speed,
    mode: Mode,
    cb: Callbacks,
    *,
    client: ModelClient,
    helper_factory: Callable[[], ModelClient] | None = None,
    helper_preapproved: bool = False,
    project: ProjectConfig | None = None,
    allow_risky: bool = False,
    allow_test_edits: bool = False,
    settings: SpeedSettings | None = None,
    resume: SolveResult | None = None,
    learn: bool = False,
    pause_s: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
    given_repro: tuple[str, str] | None = None,
) -> SolveResult:
    """Run one task end to end and return a graded, explained result (or the reason there is none).

    One job per project at a time. Never touches the user's files: all work happens in worktrees.
    """
    folder = Path(root)
    task = TaskSpec(
        id=new_task_id(), request=request, speed=speed, allow_test_edits=allow_test_edits
    )
    run = _Run(
        root=folder,
        request=request,
        speed=speed,
        mode=mode,
        cb=cb,
        client=client,
        helper_factory=helper_factory,
        helper_preapproved=helper_preapproved,
        project=project,
        allow_risky=allow_risky,
        allow_test_edits=allow_test_edits,
        settings=settings or settings_for(speed),
        resume=resume,
        learn=learn,
        pause_s=pause_s,
        sleep=sleep,
        given_repro=given_repro,
    )
    run.spec = task
    try:
        with project_lock(folder, task.id):
            try:
                return run.execute()
            finally:
                worktree.remove_all(folder)
    except ProjectBusy as exc:
        return SolveResult(status="error", task=task, message=str(exc))
    except Cancelled:
        return SolveResult(
            status="cancelled", task=task, candidates=run.pool, message="The task was cancelled."
        )
    except (ModelError, GitError) as exc:
        return SolveResult(status="error", task=task, candidates=run.pool, message=str(exc))
