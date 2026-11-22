"""TUI widgets and the pure functions that build their text (AGENTS.md section 11).

The style is calm: one accent colour (green) for grade A, plain text for everything else.
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text
from textual.widgets import Static

from qedcode.models.schemas import Mode, SolveResult, Speed

ACCENT = "bold green"
SPEEDS = (Speed.FAST, Speed.NORMAL, Speed.SURE)


def header_text(project: str, model: str, backend: str, gpu: str, state: str) -> Text:
    """`<model> . <backend> . <GPU, VRAM used/total>     [ready|loading|busy]`."""
    left = f"{model or 'no model'} · {backend or 'n/a'} · {gpu or 'n/a'}"
    # The logo as text: <∎>, with the end-of-proof square in the logo's green (assets/mark.svg).
    text = Text("<", style="bold")
    text.append("∎", style="bold #2ea043")
    text.append("> ", style="bold")
    text.append(f"QEDCode — {project}\n")
    text.append(left)
    text.append(f"    [{state}]", style="bold")
    return text


def mode_bar_text(speed: Speed, mode: Mode, tests: str) -> str:
    """`Speed: Fast [Normal] Sure        Mode: local        Tests: pytest (48)`."""
    parts = [f"[{s.value.capitalize()}]" if s is speed else s.value.capitalize() for s in SPEEDS]
    return f"Speed: {' '.join(parts)}        Mode: {mode.value}        Tests: {tests}"


@dataclass
class CardState:
    """One candidate card: its id, and either its grade or the stage it failed at."""

    id: str
    grade: str | None = None
    failed_stage: str | None = None

    def label(self) -> str:
        if self.grade:
            return f"{self.id} {self.grade}"
        if self.failed_stage:
            return f"{self.id} failed ({self.failed_stage})"
        return f"{self.id} ..."


def cards_text(cards: list[CardState]) -> str:
    """`c1 failed (targeted) | c2 B | c3 A`."""
    return " | ".join(c.label() for c in cards) if cards else ""


def grade_style(grade: str | None) -> str:
    return ACCENT if grade == "A" else "bold"


def result_text(result: SolveResult) -> Text:
    """The result panel: grade, one-line summary and the plain-English explanation."""
    text = Text()
    if result.status == "done" and result.best is not None and result.best.grade is not None:
        grade = result.best.grade.value
        text.append(f"Grade {grade}\n", style=grade_style(grade))
        text.append(f"{result.message}\n")
        if result.explanation:
            text.append(f"\n{result.explanation}\n")
        text.append("\n[a] apply   [d] diff   [e] explain   [h] try harder   [r] receipt")
        return text
    text.append(f"{result.message}\n")
    if result.question:
        text.append(f"\nQuestion for you: {result.question}\n")
    if result.status in ("no_fix", "needs_input"):
        text.append("\n[h] try harder   [m] change the speed")
    return text


class StatusHeader(Static):
    """The top line: model, backend, GPU and the state (ready, loading, busy)."""

    def show(self, project: str, model: str, backend: str, gpu: str, state: str) -> None:
        self.update(header_text(project, model, backend, gpu, state))


class ModeBar(Static):
    """The speed and mode line."""

    def show(self, speed: Speed, mode: Mode, tests: str) -> None:
        self.update(Text(mode_bar_text(speed, mode, tests)))


class CandidateCards(Static):
    """One short card per try, filled in as the engine reports them."""

    def show(self, cards: list[CardState]) -> None:
        self.update(Text(cards_text(cards)))


class ResultPanel(Static):
    """The grade, the summary and the explanation of the last result."""

    def show(self, result: SolveResult | None) -> None:
        self.update(result_text(result) if result is not None else "")
