"""The ModelClient protocol and the factory that builds a client from config (AGENTS.md section 10).

`on_token` callbacks may raise to abort a reply (the engine uses this to cancel); clients close the
stream and let the exception pass through.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

import httpx

if TYPE_CHECKING:
    from qedcode.config import GlobalConfig

CONNECT_TIMEOUT_S = 5.0
READ_TIMEOUT_S = 600.0
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"


class ModelError(RuntimeError):
    """A model could not answer. The message says what happened, why, and what to do next."""


class ServerNotRunning(ModelError):
    """No model server answered at the expected address."""


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


def default_timeout() -> httpx.Timeout:
    """Connect 5 s, read 600 s (a long generation on a small GPU can be slow)."""
    return httpx.Timeout(connect=CONNECT_TIMEOUT_S, read=READ_TIMEOUT_S, write=30.0, pool=5.0)


def ollama_base_url() -> str:
    """The Ollama address: OLLAMA_HOST when set, else the local default."""
    raw = os.environ.get("OLLAMA_HOST", "").strip()
    if not raw:
        return DEFAULT_OLLAMA_URL
    if "://" not in raw:
        raw = "http://" + raw
    if raw.count(":") < 2:  # no port given
        raw += ":11434"
    return raw.rstrip("/")


def make_client(
    config: GlobalConfig, role: Literal["main", "helper"] = "main", *, transport=None
) -> ModelClient:
    """Build the ModelClient for `role` from the global config.

    main: cloud mode uses the cloud model, otherwise the local backend. helper: the mix-mode helper.
    """
    if role == "helper":
        return _make_helper(config, transport)
    if config.engine.mode.value == "cloud":
        return _make_cloud(config.cloud.model, config.local.ctx)
    return _make_local(config, transport)


def _make_local(config: GlobalConfig, transport) -> ModelClient:
    local = config.local
    keep_alive = config.engine.keep_alive_minutes
    if local.backend == "ollama":
        from qedcode.runtime.ollama import OllamaClient

        return OllamaClient(
            model=local.model,
            base_url=ollama_base_url(),
            ctx=local.ctx,
            keep_alive_minutes=keep_alive,
            transport=transport,
        )
    if local.backend == "openai_compat":
        from qedcode.runtime.openai_compat import OpenAICompatClient

        if not local.base_url:
            raise ModelError(
                "The local backend is openai_compat but no base_url is set. "
                "Set [local] base_url (for LM Studio: http://127.0.0.1:1234/v1) in "
                "~/.qedcode/config.toml, or run `qed setup`."
            )
        return OpenAICompatClient(
            model=local.model, base_url=local.base_url, ctx=local.ctx, transport=transport
        )
    from qedcode.runtime.llamacpp import make_llamacpp_client

    return make_llamacpp_client(config)


def _make_helper(config: GlobalConfig, transport) -> ModelClient:
    helper = config.helper
    if not helper.model:
        raise ModelError(
            "No helper model is set. Choose one in `qed setup` (mix mode), or set [helper] model."
        )
    if helper.backend == "ollama":
        from qedcode.runtime.ollama import OllamaClient

        return OllamaClient(
            model=helper.model,
            base_url=ollama_base_url(),
            ctx=config.local.ctx,
            keep_alive_minutes=config.engine.keep_alive_minutes,
            transport=transport,
        )
    if helper.backend == "openai_compat":
        from qedcode.runtime.openai_compat import OpenAICompatClient

        if not helper.base_url:
            raise ModelError(
                "The helper backend is openai_compat but [helper] base_url is empty. "
                "Set it, for example http://127.0.0.1:1234/v1."
            )
        return OpenAICompatClient(
            model=helper.model, base_url=helper.base_url, ctx=config.local.ctx, transport=transport
        )
    return _make_cloud(helper.model, config.local.ctx)


def _make_cloud(model: str, ctx: int) -> ModelClient:
    from qedcode.runtime.cloud import CloudClient

    if not model:
        raise ModelError(
            "No cloud model is set. Run `qed setup` and pick a provider and model, "
            "or set [cloud] model in ~/.qedcode/config.toml."
        )
    return CloudClient(model=model, ctx=ctx)
