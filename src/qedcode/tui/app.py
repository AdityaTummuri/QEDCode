"""The terminal UI (AGENTS.md section 11). Thin: it shows Events and calls only qedcode.engine.api.

The engine runs in a Textual worker thread; its Events reach the screen through call_from_thread, so
the screen never freezes. Questions from the engine (risky code, the helper, an unclear task) are
shown as dialogs and the worker waits for the answer.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rich.text import Text
from textual.app import App, ComposeResult, SuspendNotSupported
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Footer, Input, RichLog

from qedcode.config import GlobalConfig, global_config_path, load_global, save_global
from qedcode.engine import api
from qedcode.engine.budget import next_speed
from qedcode.engine.events import event_line
from qedcode.hardware.detect import HardwareInfo, current_hardware
from qedcode.hardware.telemetry import format_vram, vram_usage
from qedcode.models.schemas import Event, Grade, Mode, SolveResult, Speed, TaskSpec
from qedcode.project.detect import (
    add_qed_to_gitignore,
    confirm_project,
    first_run,
    init_git_repo,
    install_pytest,
)
from qedcode.runtime.client import ModelClient, ModelError, make_client
from qedcode.runtime.quiet import effective_keep_alive
from qedcode.tui.screens import (
    HELP_TEXT,
    AskScreen,
    ConfirmScreen,
    DiffScreen,
    SettingsScreen,
    TextScreen,
)
from qedcode.tui.widgets import (
    SPEEDS,
    CandidateCards,
    CardState,
    ModeBar,
    ResultPanel,
    StatusHeader,
)
from qedcode.vcs.git import has_commits, is_git_repo
from qedcode.verify.receipt import load_receipt, summary_line

log = logging.getLogger("qedcode.tui")


class WorkCancelled(Exception):  # noqa: N818 - control flow inside the app
    """The task was cancelled while the app waited for the model."""


class TuiCallbacks:
    """The engine's Callbacks, shown on screen: events to the log, questions as dialogs."""

    def __init__(self, app: QedApp) -> None:
        self.app = app

    def on_event(self, event: Event) -> None:
        self.app.call_from_thread(self.app.handle_event, event)

    def ask(self, question: str) -> str | None:
        return self.app.run_dialog(AskScreen(question))

    def confirm(self, prompt: str) -> bool:
        lowered = prompt.lower()
        if "helper model" in lowered:
            screen = ConfirmScreen(
                prompt, yes="Ask the helper", no="Not now", title="Ask the helper model?"
            )
        elif "risky" in lowered:
            screen = ConfirmScreen(prompt, yes="Allow", no="Reject", title="Risky code")
        else:
            screen = ConfirmScreen(prompt)
        return bool(self.app.run_dialog(screen))

    def cancelled(self) -> bool:
        return self.app.cancel_flag.is_set()


class QedApp(App[None]):
    """QEDCode in the terminal."""

    CSS_PATH = "app.tcss"
    TITLE = "QEDCode"
    BINDINGS = [
        Binding("a", "apply", "apply"),
        Binding("d", "diff", "diff"),
        Binding("e", "explain", "explain"),
        Binding("h", "harder", "try harder"),
        Binding("u", "undo", "undo"),
        Binding("r", "receipt", "receipt"),
        Binding("m", "speed", "speed"),
        Binding("s", "settings", "settings"),
        Binding("question_mark", "help", "help"),
        Binding("q", "quit", "quit"),
        Binding("escape", "cancel", "cancel", show=False),
        Binding("slash", "focus_input", "type", show=False),
        Binding("tab", "focus_input", "type", show=False),
    ]

    def __init__(
        self,
        root: Path | str,
        *,
        config: GlobalConfig | None = None,
        client_factory: Callable[[GlobalConfig], ModelClient] | None = None,
        hardware: HardwareInfo | None = None,
        startup_checks: bool = True,
        setup_runner: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.root = Path(root)
        self.config = config or load_global()
        self.client_factory = client_factory or (lambda cfg: make_client(cfg, "main"))
        self.hw = hardware
        self.startup_checks = startup_checks
        self.setup_runner = setup_runner
        self.speed: Speed = self.config.engine.speed
        self.mode: Mode = self.config.engine.mode
        self.state = "loading"
        self.last_result: SolveResult | None = None
        self.last_request = ""
        self.cards: list[CardState] = []
        self.log_lines: list[str] = []
        self.cancel_flag = threading.Event()
        self.model_ready = threading.Event()
        self.model_error = ""
        self.client: ModelClient | None = None
        self.tests_label = "pytest"
        self._model_stopped = False

    # ------------------------------------------------------------------ layout

    def compose(self) -> ComposeResult:
        yield StatusHeader(id="header")
        yield ModeBar(id="modebar")
        yield RichLog(id="log", wrap=True, markup=False, highlight=False)
        yield CandidateCards(id="cards")
        yield ResultPanel(id="result")
        yield Input(
            placeholder="> describe what you want done (Enter to run, /help for commands)",
            id="task",
        )
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_chrome()
        self.set_interval(5.0, self.refresh_chrome)
        self.query_one("#task", Input).focus()
        self.write_log("QEDCode is ready when the header says [ready]. Type a task below.")
        self.run_worker(self._startup(), exclusive=True, name="startup")

    # ------------------------------------------------------------------ small helpers

    def write_log(self, line: str, style: str = "") -> None:
        self.log_lines.append(line)
        self.query_one("#log", RichLog).write(Text(line, style=style))

    def set_state(self, state: str) -> None:
        self.state = state
        self.refresh_chrome()

    def model_name(self) -> str:
        if self.client is not None:
            return self.client.name
        cfg = self.config
        return cfg.cloud.model if self.mode is Mode.CLOUD else cfg.local.model

    def backend_name(self) -> str:
        if self.mode is Mode.CLOUD:
            return "cloud"
        return self.config.local.backend

    def gpu_text(self) -> str:
        hw = self.hw
        if hw is None:
            return "n/a"
        name = hw.gpu_name or hw.vendor
        if hw.vendor in ("cpu", ""):
            return "CPU only"
        return f"{name}, {format_vram(vram_usage(hw.vendor))}"

    def refresh_chrome(self) -> None:
        if not self.is_mounted:
            return
        self.query_one("#header", StatusHeader).show(
            self.root.name, self.model_name(), self.backend_name(), self.gpu_text(), self.state
        )
        self.query_one("#modebar", ModeBar).show(self.speed, self.mode, self.tests_label)

    def run_dialog(self, screen: ModalScreen[Any]) -> Any:
        """Show a dialog from the worker thread and wait for the answer (None if cancelled)."""
        done = threading.Event()
        holder: dict[str, Any] = {}

        def finished(value: Any) -> None:
            holder["value"] = value
            done.set()

        self.call_from_thread(self.push_screen, screen, finished)
        while not done.wait(0.1):
            if self.cancel_flag.is_set():
                return None
        return holder.get("value")

    # ------------------------------------------------------------------ startup

    async def _startup(self) -> None:
        if self.startup_checks:
            if not await self._ensure_global_setup():
                return
            if not await self._ensure_git():
                return
            await self._ensure_project_config()
        self._begin_model_load()

    async def _ensure_global_setup(self) -> bool:
        if global_config_path().exists():
            return True
        wants = await self.push_screen_wait(
            ConfirmScreen(
                "QEDCode has not been set up on this computer yet: it needs to know your "
                "hardware and which model to use.\n\nRun the setup now? (It asks before "
                "downloading anything.)",
                yes="Run setup",
                no="Not now",
                title="First run",
            )
        )
        if wants and self.setup_runner is not None:
            self._run_in_terminal(self.setup_runner)
            self.config = load_global()
            self.speed, self.mode = self.config.engine.speed, self.config.engine.mode
        elif not wants:
            self.write_log("Skipping setup. Run `qed setup` any time to choose a model.")
        return True

    def _run_in_terminal(self, function: Callable[[], None]) -> None:
        """Run terminal-style code (the setup wizard) with the screen suspended, if possible."""
        try:
            with self.suspend():
                function()
        except SuspendNotSupported:
            function()

    async def _ensure_git(self) -> bool:
        if is_git_repo(self.root) and has_commits(self.root):
            return True
        repo = is_git_repo(self.root)
        question = (
            "This repository has no commits yet. QEDCode works in safe copies made with git, "
            "so it needs a first commit.\n\nMake a first commit of your current files?"
            if repo
            else "This folder is not a git repository. QEDCode works in safe copies made with "
            "git.\n\nSet one up now? (git init, then a first commit of your current files.)"
        )
        if await self.push_screen_wait(ConfirmScreen(question, yes="Set it up", no="Quit")):
            await asyncio.to_thread(init_git_repo, self.root)
            self.write_log("Created the git repository and a first commit.")
            return True
        self.exit(message="QEDCode needs a git repository to work safely. Nothing was changed.")
        return False

    async def _ensure_project_config(self) -> None:
        first = await asyncio.to_thread(first_run, self.root)
        detection = first.detection
        runner = first.config.test_runner
        if first.already_confirmed or detection is None:
            self.tests_label = f"{runner} ({self._count_label(first)})"
            return
        self.tests_label = f"{detection.runner} ({detection.test_count})"
        if detection.language == "javascript":
            found = detection.describe()
        else:
            version = detection.python_version or "could not run"
            found = (
                f"  Python: {detection.python}"
                f" ({version}, from {detection.python_source})\n"
                f"  Tests: pytest {detection.pytest_version or 'not installed'}; "
                f"{detection.test_count} tests in {len(detection.test_files)} files\n"
            )
        text = (
            "QEDCode found these settings for this project:\n"
            + found
            + f"  Never edited: {', '.join(first.config.protected[:6])} ...\n"
        )
        if detection.notes:
            text += "\nNotes:\n" + "\n".join(f"  - {n}" for n in detection.notes)
        text += "\n\nUse these settings?"
        if await self.push_screen_wait(ConfirmScreen(text, yes="Use them", no="Not now")):
            confirm_project(first)
            self.write_log("Saved the project settings to .qed/project.toml.")
            if detection.language == "python" and not detection.pytest_ok:
                await self._offer_pytest(detection.python)
            if not first.qed_in_gitignore and await self.push_screen_wait(
                ConfirmScreen("Add .qed/ to your .gitignore?", yes="Add it", no="No thanks")
            ):
                add_qed_to_gitignore(self.root)
        else:
            self.write_log("Using the detected settings for this session only.")

    async def _offer_pytest(self, python: str) -> None:
        wants = await self.push_screen_wait(
            ConfirmScreen(
                f"pytest is not installed for {python}. Install it there now?", yes="Install"
            )
        )
        if wants:
            result = await asyncio.to_thread(install_pytest, python, self.root, lambda _p: True)
            ok = result is not None and result.ok
            self.write_log(
                "Installed pytest." if ok else "Could not install pytest; see `qed doctor`."
            )

    @staticmethod
    def _count_label(first: Any) -> str:
        return str(first.detection.test_count) if first.detection else "?"

    # ------------------------------------------------------------------ the model

    def _begin_model_load(self) -> None:
        self.set_state("loading")
        self.run_worker(self._load_model_job, thread=True, name="model", exclusive=False)

    def _load_model_job(self) -> None:
        try:
            self.hw = self.hw or current_hardware()
            self.call_from_thread(self.refresh_chrome)
            client = self.client_factory(self.config)
            starter = getattr(client, "ensure_started", None)
            if callable(starter):
                self.call_from_thread(self.write_log, "Loading model...")
                starter(on_status=lambda msg: self.call_from_thread(self.write_log, msg))
            self.client = client
            manager = getattr(client, "manager", None)
            if manager is not None and hasattr(manager, "start_idle_watcher"):
                manager.start_idle_watcher(effective_keep_alive(self.config))
        except ModelError as exc:
            self.model_error = str(exc)
        except Exception as exc:  # noqa: BLE001 - never let a loading bug kill the screen
            log.error("Model load failed\n%s", traceback.format_exc())
            self.model_error = f"The model could not be loaded ({type(exc).__name__}: {exc})."
        finally:
            self.model_ready.set()
            self.call_from_thread(self._model_loaded)

    def _model_loaded(self) -> None:
        if self.model_error:
            self.write_log(self.model_error)
            self.write_log("Fix that, then press s (settings) or restart. Tasks cannot run yet.")
        if self.state != "busy":
            self.set_state("ready")
        self.refresh_chrome()

    def _wait_for_model(self, cb: TuiCallbacks) -> ModelClient:
        if not self.model_ready.is_set():
            cb.on_event(Event(kind="log", message="Waiting for the model to load..."))
        while not self.model_ready.wait(0.2):
            if self.cancel_flag.is_set():
                raise WorkCancelled
        if self.client is None:
            raise ModelError(
                self.model_error
                or "The model is not available. Check the messages above, then restart QEDCode."
            )
        return self.client

    # ------------------------------------------------------------------ running tasks

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        if text.startswith("/"):
            self.handle_command(text)
        else:
            self.start_task(text)
        self.query_one("#log", RichLog).focus()

    def start_task(
        self, request: str, *, resume: SolveResult | None = None, speed: Speed | None = None
    ) -> None:
        if self.state == "busy":
            self.write_log("A task is already running. Press Esc to cancel it first.")
            return
        self.last_request = request
        self.cards = []
        self.query_one("#cards", CandidateCards).show([])
        self.query_one("#result", ResultPanel).show(None)
        self.cancel_flag.clear()
        self.set_state("busy")
        self.write_log(f"> {request}")
        self.run_worker(
            lambda: self._solve_job(request, resume, speed or self.speed),
            thread=True,
            exclusive=True,
            group="job",
            name="solve",
        )

    def _solve_job(self, request: str, resume: SolveResult | None, speed: Speed) -> None:
        cb = TuiCallbacks(self)
        try:
            client = self._wait_for_model(cb)
            result = api.solve(
                self.root,
                request,
                speed=speed,
                mode=self.mode,
                cb=cb,
                config=self.config,
                client=client,
                resume=resume,
            )
        except WorkCancelled:
            result = self._plain_result(request, speed, "cancelled", "The task was cancelled.")
        except ModelError as exc:
            result = self._plain_result(request, speed, "error", str(exc))
        except Exception as exc:  # noqa: BLE001 - a friendly message; the details go to the log
            log.error("Task crashed\n%s", traceback.format_exc())
            result = self._plain_result(
                request,
                speed,
                "error",
                f"Something went wrong inside QEDCode ({type(exc).__name__}). "
                "The details were saved to the log; please report it.",
            )
        self.call_from_thread(self.finish_task, result)

    @staticmethod
    def _plain_result(request: str, speed: Speed, status: str, message: str) -> SolveResult:
        return SolveResult(
            status=status,  # type: ignore[arg-type]
            task=TaskSpec(id="t-none", request=request, speed=speed),
            message=message,
        )

    def handle_event(self, event: Event) -> None:
        line = event_line(event)
        if line:
            self.write_log(line)
        if event.kind == "candidate":
            cid = str(event.data.get("candidate", ""))
            grade = event.data.get("grade")
            stage = event.data.get("failed_stage")
            card = next((c for c in self.cards if c.id == cid), None)
            if card is None:
                card = CardState(cid)
                self.cards.append(card)
            card.grade, card.failed_stage = grade, stage
            self.query_one("#cards", CandidateCards).show(self.cards)

    def finish_task(self, result: SolveResult) -> None:
        self.last_result = result
        self.set_state("ready" if self.model_ready.is_set() else "loading")
        self.query_one("#result", ResultPanel).show(result)
        if result.status != "done":
            self.write_log(result.message)
        self.query_one("#log", RichLog).focus()

    # ------------------------------------------------------------------ slash commands

    def handle_command(self, text: str) -> None:
        command, _, arg = text.partition(" ")
        arg = arg.strip()
        if command == "/help":
            self.action_help()
        elif command == "/speed":
            self._set_speed_by_name(arg)
        elif command == "/models":
            self._show_models()
        elif command == "/check":
            self._start_check()
        elif command == "/verify":
            self._start_verify(arg)
        else:
            self.write_log(f"Unknown command {command}. Type /help to see them.")

    def _set_speed_by_name(self, name: str) -> None:
        try:
            self.speed = Speed(name.lower())
        except ValueError:
            self.write_log("Use /speed fast, /speed normal or /speed sure.")
            return
        self.refresh_chrome()
        self.write_log(f"Speed is now {self.speed.value}.")

    def _show_models(self) -> None:
        self.write_log(f"Using: {self.model_name()} ({self.backend_name()})")
        try:
            from qedcode.hardware.recommend import recommend
            from qedcode.runtime.catalog import load_catalog

            hw = self.hw or current_hardware()
            rec = recommend(hw, load_catalog())
            if rec.model_id:
                self.write_log(f"Recommended for this computer: {rec.display}, {rec.ctx} tokens")
        except Exception:  # noqa: BLE001 - informational only
            self.write_log("Run `qed models list` for the models QEDCode knows.")

    def _start_check(self) -> None:
        if self.state == "busy":
            self.write_log("A task is already running. Press Esc to cancel it first.")
            return
        self.cancel_flag.clear()
        self.set_state("busy")
        self.write_log("> /check")
        self.run_worker(self._check_job, thread=True, exclusive=True, group="job", name="check")

    def _check_job(self) -> None:
        cb = TuiCallbacks(self)
        outcome = api.check(self.root, speed=self.speed, cb=cb)
        self.call_from_thread(self._finish_check, outcome)

    def _finish_check(self, outcome: Any) -> None:
        self.set_state("ready")
        lines = [outcome.summary or outcome.message]
        if outcome.failing_tests:
            lines += ["Failing tests:"] + [f"  - {k}" for k in outcome.failing_tests[:8]]
        if outcome.receipt_path:
            lines.append(f"Receipt: {outcome.receipt_path}")
        self.query_one("#result", ResultPanel).update(Text("\n".join(lines)))
        for line in lines:
            self.write_log(line)

    def _start_verify(self, path: str) -> None:
        if not path:
            self.write_log("Usage: /verify <receipt file>")
            return
        self.write_log(f"> /verify {path}")
        self.run_worker(lambda: self._verify_job(path), thread=True, group="verify", name="verify")

    def _verify_job(self, path: str) -> None:
        result = api.verify(path, self.root)
        lines = (
            ["VERIFIED"]
            if result.verified
            else ["NOT VERIFIED"] + [f"- {m}" for m in result.mismatches]
        )
        for line in lines:
            self.call_from_thread(self.write_log, line)

    # ------------------------------------------------------------------ actions (keys)

    def _best_result(self) -> SolveResult | None:
        result = self.last_result
        if result is None or result.status != "done" or result.best is None:
            self.write_log("There is no result yet. Run a task first.")
            return None
        return result

    def action_focus_input(self) -> None:
        self.query_one("#task", Input).focus()

    def action_cancel(self) -> None:
        if self.state == "busy":
            if not self.cancel_flag.is_set():
                self.cancel_flag.set()
                self.write_log("Cancelling... (it stops at the next safe point)")
        else:
            self.query_one("#log", RichLog).focus()

    def action_help(self) -> None:
        self.push_screen(TextScreen("QEDCode help", HELP_TEXT))

    def action_diff(self) -> None:
        result = self._best_result()
        if result is not None and result.best is not None:
            self.push_screen(DiffScreen(result.best.diff, f"Diff of {result.best.id}"))

    def action_explain(self) -> None:
        result = self._best_result()
        if result is not None:
            self.push_screen(
                TextScreen("What changed and why", result.explanation or result.message)
            )

    def action_receipt(self) -> None:
        result = self._best_result()
        if result is None or not result.receipt_path:
            return
        try:
            receipt = load_receipt(result.receipt_path)
        except ValueError as exc:
            self.write_log(str(exc))
            return
        body = (
            f"{summary_line(receipt)}\n\nReceipt file:\n  {result.receipt_path}\n\n"
            "Checks that ran:\n"
            + "\n".join(
                f"  {c.stage}: {'ok' if c.passed else 'FAILED'} - {c.summary}"
                for c in receipt.checks
            )
            + "\n\nNot proven (plain English):\n"
            + "\n".join(f"  - {g}" for g in receipt.unverified)
            + f"\n\nRe-check it any time:  qed verify {result.receipt_path}"
        )
        self.push_screen(TextScreen("Proof receipt", body))

    def action_speed(self) -> None:
        index = SPEEDS.index(self.speed)
        self.speed = SPEEDS[(index + 1) % len(SPEEDS)]
        self.refresh_chrome()
        self.write_log(f"Speed is now {self.speed.value}.")

    def action_harder(self) -> None:
        previous = self.last_result
        if previous is None or not self.last_request:
            self.write_log("There is nothing to retry yet. Run a task first.")
            return
        if previous.status == "cancelled":
            self.write_log("The last task was cancelled; run it again instead.")
            return
        self.speed = next_speed(self.speed)
        self.refresh_chrome()
        self.write_log(f"Trying harder at {self.speed.value} speed, starting from the best try...")
        self.start_task(self.last_request, resume=previous, speed=self.speed)

    def action_apply(self) -> None:
        result = self._best_result()
        if result is None or result.best is None:
            return
        grade = result.best.grade
        if grade in (Grade.C, Grade.D):
            self.push_screen(
                ConfirmScreen(
                    f"This change has grade {grade.value}: no test proves it. Read the diff first "
                    "(press d), then decide.\n\nApply it to your files anyway?",
                    yes="Apply anyway",
                    no="Not now",
                    title=f"Grade {grade.value}",
                ),
                lambda ok: self._do_apply(result.task.id, True) if ok else None,
            )
            return
        self._do_apply(result.task.id, False)

    def _do_apply(self, task_id: str, allow_low: bool) -> None:
        self.run_worker(
            lambda: self._apply_job(task_id, allow_low), thread=True, group="apply", name="apply"
        )

    def _apply_job(self, task_id: str, allow_low: bool) -> None:
        outcome = api.apply_result(self.root, task_id, allow_low_grade=allow_low)
        self.call_from_thread(self.write_log, outcome.message)

    def action_undo(self) -> None:
        self.run_worker(self._undo_job, thread=True, group="apply", name="undo")

    def _undo_job(self) -> None:
        outcome = api.undo(self.root)
        self.call_from_thread(self.write_log, outcome.message)

    def action_settings(self) -> None:
        values = {
            "mode": self.mode.value,
            "model": self.config.local.model,
            "keep_alive": self.config.engine.keep_alive_minutes,
            "helper": self.config.helper.model,
        }
        self.push_screen(SettingsScreen(values), self._settings_done)

    def _settings_done(self, new: dict | None) -> None:
        if not new:
            return
        cfg = self.config
        cfg.engine.mode = Mode(new["mode"])
        cfg.engine.keep_alive_minutes = new["keep_alive"]
        cfg.helper.model = new["helper"]
        cfg.helper.enabled = bool(new["helper"])
        model_changed = new["model"] and new["model"] != cfg.local.model
        if model_changed:
            cfg.local.model = new["model"]
        self.mode = cfg.engine.mode
        try:
            save_global(cfg)
        except OSError as exc:
            self.write_log(f"Could not save the settings ({exc}).")
        self.refresh_chrome()
        self.write_log(
            "Settings saved." + (" Restart QEDCode to load the new model." if model_changed else "")
        )

    async def action_quit(self) -> None:
        self.cancel_flag.set()
        self.stop_model()
        self.exit()

    def stop_model(self) -> None:
        """Stop watching the model server and unload the model if the config says so."""
        if self._model_stopped:
            return
        self._model_stopped = True
        client = self.client
        if client is None:
            return
        manager = getattr(client, "manager", None)
        if manager is not None and hasattr(manager, "stop_idle_watcher"):
            manager.stop_idle_watcher()
        if self.config.engine.unload_on_exit:
            try:
                client.unload()
            except Exception:  # noqa: BLE001 - unloading is best effort
                log.warning("Could not unload the model", exc_info=True)

    def on_unmount(self) -> None:
        self.stop_model()


def run_tui(root: Path | str | None = None) -> None:
    """Open the TUI in `root` (default: the current folder)."""
    from qedcode.setup_wizard import RichPrompter, run_setup

    app = QedApp(
        Path(root) if root is not None else Path.cwd(),
        setup_runner=lambda: run_setup(RichPrompter()),
    )
    app.run()
