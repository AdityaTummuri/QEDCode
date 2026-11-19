"""FakeModel: a scripted ModelClient for tests. It only replays what the script says."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROLE_TAGS = ("intake", "locator", "test_writer", "coder", "repairer", "explainer")


class FakeModelError(RuntimeError):
    """Raised when a test asks the FakeModel for a reply the script does not have."""


@dataclass
class RecordedCall:
    """One chat() call, kept for assertions."""

    messages: list[dict]
    temperature: float
    max_tokens: int
    stop: list[str] | None
    tag: str

    @property
    def prompt(self) -> str:
        """All message contents joined, handy for `assert "x" in call.prompt`."""
        return "\n".join(str(m.get("content", "")) for m in self.messages)


def load_script(path: Path | str) -> dict[str, list[str]]:
    """Read a YAML script file: a mapping of tag -> list of reply strings."""
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise FakeModelError(f"Cannot read the FakeModel script {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise FakeModelError(f"The FakeModel script {path} is not valid YAML: {exc}") from exc
    return _validate_script(data, str(path))


def _validate_script(data: Any, where: str) -> dict[str, list[str]]:
    if not isinstance(data, dict):
        raise FakeModelError(f"A FakeModel script ({where}) must map tags to lists of replies.")
    script: dict[str, list[str]] = {}
    for tag, replies in data.items():
        if isinstance(replies, str):
            replies = [replies]
        if not isinstance(replies, list) or not all(isinstance(r, str) for r in replies):
            raise FakeModelError(
                f"In {where}, the replies for tag '{tag}' must be a list of strings."
            )
        script[str(tag)] = list(replies)
    return script


@dataclass
class FakeModel:
    """Returns scripted replies in order for each tag and records every call."""

    script: dict[str, list[str]]
    name: str = "fake:scripted"
    context_tokens: int = 16384
    calls: list[RecordedCall] = field(default_factory=list)
    unload_count: int = 0

    def __post_init__(self) -> None:
        self.script = _validate_script(self.script, "the script argument")
        self._cursor: dict[str, int] = {tag: 0 for tag in self.script}
        self._lock = threading.Lock()

    @classmethod
    def from_yaml(cls, path: Path | str, **kwargs: Any) -> FakeModel:
        """Build a FakeModel from a YAML script file."""
        return cls(load_script(path), **kwargs)

    # ----------------------------------------------------------------- ModelClient

    def chat(
        self,
        messages: list[dict],
        *,
        temperature: float,
        max_tokens: int,
        stop: list[str] | None = None,
        on_token: Callable[[str], None] | None = None,
        tag: str = "",
    ) -> str:
        with self._lock:
            self.calls.append(
                RecordedCall(
                    messages=[dict(m) for m in messages],
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stop=list(stop) if stop else None,
                    tag=tag,
                )
            )
            if tag not in self.script:
                known = ", ".join(sorted(self.script)) or "none"
                raise FakeModelError(
                    f"FakeModel has no script for tag '{tag}'. Tags with a script: {known}. "
                    "Add a list of replies for this tag to the script."
                )
            index = self._cursor[tag]
            replies = self.script[tag]
            if index >= len(replies):
                raise FakeModelError(
                    f"FakeModel ran out of replies for tag '{tag}': the script has "
                    f"{len(replies)} and this is call number {index + 1}. "
                    "Add more replies for this tag, or check that the engine is not looping."
                )
            self._cursor[tag] = index + 1
            reply = replies[index]
        if on_token is not None:
            on_token(reply)
        return reply

    def health(self) -> bool:
        return True

    def unload(self) -> None:
        self.unload_count += 1

    # ----------------------------------------------------------------- assertions

    def calls_for(self, tag: str) -> list[RecordedCall]:
        """Return the recorded calls made with this tag, in order."""
        return [c for c in self.calls if c.tag == tag]

    def remaining(self, tag: str) -> int:
        """How many scripted replies for `tag` are still unused."""
        return len(self.script.get(tag, [])) - self._cursor.get(tag, 0)
