"""One place that turns an engine Event into a line of text for a log or a progress list."""

from __future__ import annotations

from qedcode.models.schemas import Event

SHOWN = ("stage", "candidate", "log", "result", "check", "warning")


def event_line(event: Event, *, include_result: bool = True) -> str | None:
    """The progress line for an event, or None for events that are not shown (token streams)."""
    if event.kind == "result" and not include_result:
        return None
    if event.kind in ("stage", "candidate", "log", "result"):
        return event.message
    if event.kind == "check":
        return "  " + event.message
    if event.kind == "warning":
        return f"Warning: {event.message}"
    return None
