"""CloudClient: any hosted model through LiteLLM; the key comes from keyring or the environment.

Code sent through this client leaves the computer (mix and cloud modes); the UI says so first.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from qedcode.runtime.client import ModelError
from qedcode.runtime.secrets import (
    env_var_for,
    get_api_key,
    mask_secrets,
    needs_key,
    provider_of,
)

log = logging.getLogger("qedcode.cloud")
CHARS_PER_TOKEN = 3.5


def _load_litellm() -> Any:
    """Import litellm on first use (it is slow to import)."""
    import litellm

    litellm.drop_params = True  # ignore settings a provider does not support
    return litellm


class _CallbackFailed(Exception):  # noqa: N818 - internal wrapper, never shown to users
    """Carries an exception raised by an on_token callback out of the provider error handling."""

    def __init__(self, error: BaseException) -> None:
        super().__init__(str(error))
        self.error = error


class CloudClient:
    """A ModelClient over litellm.completion with streaming and token counting."""

    backend = "cloud"
    is_remote = True

    def __init__(self, model: str, *, ctx: int = 32768) -> None:
        self.model = model
        self.provider = provider_of(model)
        self.name = f"cloud:{model}"
        self.context_tokens = max(ctx, 32768)
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}

    # ------------------------------------------------------------------ ModelClient

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
        key = self._key()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if stop:
            kwargs["stop"] = stop
        if key:
            kwargs["api_key"] = key
        litellm = _load_litellm()
        pieces: list[str] = []
        usage: Any = None
        try:
            for chunk in litellm.completion(**kwargs):
                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage:
                    usage = chunk_usage
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                piece = getattr(choices[0].delta, "content", None) or ""
                if piece:
                    pieces.append(piece)
                    if on_token is not None:
                        try:
                            on_token(piece)
                        except BaseException as stop_now:
                            raise _CallbackFailed(stop_now) from None
        except _CallbackFailed as wrapped:
            raise wrapped.error from None  # a cancel raised by on_token passes through untouched
        except ModelError:
            raise
        except Exception as exc:  # noqa: BLE001 - litellm raises many provider-specific errors
            raise self._explain(exc, key) from None
        reply = "".join(pieces)
        self._record_usage(usage, messages, reply)
        return reply

    def health(self) -> bool:
        """True when a key is available (or none is needed). No network call is made."""
        return not needs_key(self.provider) or get_api_key(self.provider) is not None

    def unload(self) -> None:
        """Nothing to unload: the model runs on the provider's servers."""

    # ------------------------------------------------------------------ tokens

    def count_tokens(self, messages: list[dict]) -> int:
        """Estimate the tokens in `messages` (litellm's counter, else characters / 3.5)."""
        try:
            return int(_load_litellm().token_counter(model=self.model, messages=messages))
        except Exception:  # noqa: BLE001 - counting is best effort
            chars = sum(len(str(m.get("content", ""))) for m in messages)
            return int(chars / CHARS_PER_TOKEN) + 1

    def _record_usage(self, usage: Any, messages: list[dict], reply: str) -> None:
        prompt = getattr(usage, "prompt_tokens", None)
        completion = getattr(usage, "completion_tokens", None)
        if prompt is None:
            prompt = self.count_tokens(messages)
        if completion is None:
            completion = int(len(reply) / CHARS_PER_TOKEN) + (1 if reply else 0)
        self.usage["prompt_tokens"] += int(prompt)
        self.usage["completion_tokens"] += int(completion)
        self.usage["total_tokens"] += int(prompt) + int(completion)
        self.usage["calls"] += 1

    # ------------------------------------------------------------------ errors

    def _key(self) -> str | None:
        if not needs_key(self.provider):
            return None
        key = get_api_key(self.provider)
        if key is None:
            raise ModelError(
                f"There is no API key for {self.provider}, so the cloud model cannot answer. "
                f"Run `qed setup` to save one in your system keychain, or set the "
                f"{env_var_for(self.provider)} environment variable."
            )
        return key

    def _explain(self, exc: Exception, key: str | None) -> ModelError:
        kind = type(exc).__name__
        detail = mask_secrets(str(exc), [key])[:300]
        log.warning("Cloud call to %s failed: %s: %s", self.name, kind, detail)
        who = f"{self.provider} ({self.model})"
        if "Authentication" in kind or "PermissionDenied" in kind:
            text = (
                f"{who} rejected the API key. Check that the key is right and has access to this "
                "model, then save it again with `qed setup`."
            )
        elif "RateLimit" in kind:
            text = (
                f"{who} says you are sending too many requests or are out of credit. "
                "Wait a bit, or check your plan."
            )
        elif "NotFound" in kind or ("BadRequest" in kind and "model" in detail.lower()):
            text = (
                f"{who} does not know that model name. Check the model string (for example "
                "openrouter/anthropic/claude-sonnet-4) in `qed setup`."
            )
        elif "Timeout" in kind or "Connection" in kind or "ServiceUnavailable" in kind:
            text = f"QEDCode could not reach {who}. Check your internet connection and try again."
        else:
            text = f"{who} could not answer ({kind})."
        return ModelError(f"{text} Details: {detail}")
