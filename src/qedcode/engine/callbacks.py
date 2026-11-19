"""Callbacks for runs with no human at the keyboard (CLI --json, CI, MCP): ask -> None, no yes."""

from __future__ import annotations

from collections.abc import Callable

from qedcode.models.schemas import Event


class NonInteractiveCallbacks:
    """Implements the Callbacks protocol without ever asking: ask -> None, confirm -> pre-set."""

    def __init__(
        self,
        on_event: Callable[[Event], None] | None = None,
        *,
        confirm_answer: bool = False,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self._on_event = on_event
        self._confirm = confirm_answer
        self._cancelled = cancelled
        self.events: list[Event] = []
        self.prompts: list[str] = []

    def on_event(self, event: Event) -> None:
        self.events.append(event)
        if self._on_event is not None:
            self._on_event(event)

    def ask(self, question: str) -> str | None:
        self.prompts.append(question)
        return None

    def confirm(self, prompt: str) -> bool:
        self.prompts.append(prompt)
        return self._confirm

    def cancelled(self) -> bool:
        return bool(self._cancelled and self._cancelled())
