"""Modal screens: confirmations, questions, the diff, receipt/explain/help text and settings."""

from __future__ import annotations

from rich.syntax import Syntax
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static

HELP_TEXT = """\
Type a task and press Enter. QEDCode tries several fixes, tests each one in a safe copy of your
project and shows the best proven result. Your files change only when you press a (apply).

  Enter   run the task in the box
  a       apply the result to your files (asks first for grade C or D)
  d       full-screen diff
  e       explain the change in plain words
  h       try harder: run again at the next speed, starting from the best try
  u       undo the last apply
  r       receipt: the proof, and how to re-check it
  m       change the speed: fast, normal, sure
  s       settings (mode, model, keep-alive, helper)
  Esc     cancel the running task
  /       type a command or a new task (Tab also works)
  ?       this help
  q       quit (the model is unloaded if no one else uses it)

Commands in the box: /check   /verify <file>   /models   /speed <name>   /help

Grades: A proven (a test failed before, passes now, nothing else broke), B tested, C checked
only, D could not be checked.
"""


class ConfirmScreen(ModalScreen[bool]):
    """A yes/no question. y = yes, n or Esc = no."""

    BINDINGS = [
        Binding("y", "answer(True)", "yes"),
        Binding("n", "answer(False)", "no"),
        Binding("escape", "answer(False)", "no"),
    ]

    def __init__(self, prompt: str, *, yes: str = "Yes", no: str = "No", title: str = "") -> None:
        super().__init__()
        self.prompt = prompt
        self.yes_label, self.no_label, self.title_text = yes, no, title

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            if self.title_text:
                yield Label(self.title_text, id="dialog-title")
            with VerticalScroll(id="dialog-body"):
                yield Static(self.prompt, markup=False)
            with Horizontal(id="dialog-buttons"):
                yield Button(f"{self.yes_label} (y)", id="yes")
                yield Button(f"{self.no_label} (n)", id="no")

    def action_answer(self, value: bool) -> None:
        self.dismiss(value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")


class AskScreen(ModalScreen[str | None]):
    """A question with a text answer. Enter sends it; Esc skips (None)."""

    BINDINGS = [Binding("escape", "skip", "skip")]

    def __init__(self, question: str) -> None:
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self.question, markup=False)
            yield Input(placeholder="Your answer (Enter to send, Esc to skip)", id="answer")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def action_skip(self) -> None:
        self.dismiss(None)


class TextScreen(ModalScreen[None]):
    """Read-only text (explanation, receipt, help). Esc or q closes it."""

    BINDINGS = [Binding("escape", "close", "close"), Binding("q", "close", "close")]

    def __init__(self, title: str, body: str) -> None:
        super().__init__()
        self.title_text, self.body = title, body

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self.title_text, id="dialog-title")
            with VerticalScroll(id="dialog-body"):
                yield Static(self.body, markup=False)
            yield Label("Esc to close", id="dialog-hint")

    def action_close(self) -> None:
        self.dismiss(None)


class DiffScreen(ModalScreen[None]):
    """The full diff with syntax highlighting. Esc or q closes it."""

    BINDINGS = [Binding("escape", "close", "close"), Binding("q", "close", "close")]

    def __init__(self, diff: str, title: str = "Diff") -> None:
        super().__init__()
        self.diff, self.title_text = diff, title

    def compose(self) -> ComposeResult:
        with Vertical(id="diff-screen"):
            yield Label(f"{self.title_text} (Esc to close)", id="dialog-title")
            with VerticalScroll(id="diff-body"):
                yield Static(
                    Syntax(self.diff or "(no changes)", "diff", theme="ansi_dark", word_wrap=False)
                )

    def action_close(self) -> None:
        self.dismiss(None)


class SettingsScreen(ModalScreen[dict | None]):
    """Mode, model, keep-alive and helper. Returns the new values, or None when cancelled."""

    BINDINGS = [Binding("escape", "cancel", "cancel")]

    def __init__(self, values: dict) -> None:
        super().__init__()
        self.values = values

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Settings", id="dialog-title")
            yield Label("Mode")
            yield Select(
                [("local", "local"), ("mix", "mix"), ("cloud", "cloud")],
                value=self.values["mode"],
                allow_blank=False,
                id="mode",
            )
            yield Label("Model (catalog id, Ollama tag or .gguf path)")
            yield Input(value=self.values["model"], id="model")
            yield Label("Keep the model loaded for this many idle minutes")
            yield Input(value=str(self.values["keep_alive"]), id="keep_alive")
            yield Label("Helper model for mix mode (empty = none)")
            yield Input(value=self.values["helper"], id="helper")
            with Horizontal(id="dialog-buttons"):
                yield Button("Save", id="save")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "save":
            self.dismiss(None)
            return
        try:
            minutes = max(1, int(self.query_one("#keep_alive", Input).value))
        except ValueError:
            minutes = int(self.values["keep_alive"])
        select = self.query_one("#mode", Select)
        self.dismiss(
            {
                "mode": str(select.value),
                "model": self.query_one("#model", Input).value.strip(),
                "keep_alive": minutes,
                "helper": self.query_one("#helper", Input).value.strip(),
            }
        )

    def action_cancel(self) -> None:
        self.dismiss(None)
