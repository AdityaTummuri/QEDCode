"""The ModelClient protocol: the only way the engine talks to a model (AGENTS.md section 10)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable


@runtime_checkable
class ModelClient(Protocol):
    """A chat model. Backends: llamacpp, ollama, openai_compat, cloud, fake."""

    name: str  # "llamacpp:qwen3.5-4b-q4km", "cloud:openrouter/..."
    context_tokens: int

    def chat(
        self,
        messages: list[dict],
        *,
        temperature: float,
        max_tokens: int,
        stop: list[str] | None = None,
        on_token: Callable[[str], None] | None = None,
        tag: str = "",  # tag = role name, for logs and FakeModel
    ) -> str: ...

    def health(self) -> bool: ...

    def unload(self) -> None: ...
