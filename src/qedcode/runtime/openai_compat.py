"""OpenAICompatClient: any OpenAI-style /chat/completions server (LM Studio, llama-server, ...)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx

from qedcode.runtime.client import ModelError, ServerNotRunning, default_timeout


def explain_http_error(response: httpx.Response, who: str, hint: str = "") -> ModelError:
    """Turn a failed HTTP response into a plain-English ModelError."""
    try:
        body = response.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - reading the body is best effort
        body = ""
    detail = body.strip()
    try:
        data = json.loads(body)
        if isinstance(data, dict):
            err = data.get("error")
            detail = (err.get("message") if isinstance(err, dict) else err) or detail
    except (json.JSONDecodeError, AttributeError):
        pass
    detail = str(detail)[:300]
    text = f"{who} answered with HTTP {response.status_code}: {detail or 'no details'}."
    return ModelError(f"{text} {hint}".strip())


def unreachable(who: str, base_url: str, start_hint: str) -> ServerNotRunning:
    """The error for 'nothing is listening there'."""
    return ServerNotRunning(
        f"QEDCode could not reach {who} at {base_url}, so no model answered. {start_hint}"
    )


def timed_out(who: str, seconds: float) -> ModelError:
    return ModelError(
        f"{who} took longer than {int(seconds)} seconds to answer. The model may be too big for "
        "this computer or still loading. Try the Fast speed, a smaller model, or a smaller context."
    )


def parse_sse_data(line: str) -> str | None:
    """Return the payload of an SSE `data:` line, or None for blank lines and comments."""
    line = line.strip()
    if not line.startswith("data:"):
        return None
    return line[5:].strip()


class OpenAICompatClient:
    """A ModelClient for servers that speak POST {base_url}/chat/completions with streaming."""

    backend = "openai_compat"

    def __init__(
        self,
        model: str,
        base_url: str,
        *,
        ctx: int = 16384,
        api_key: str | None = None,
        extra_body: dict[str, Any] | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: httpx.Timeout | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.context_tokens = ctx
        self.name = f"{self.backend}:{model}"
        self._extra_body = dict(extra_body or {})
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http = httpx.Client(
            timeout=timeout or default_timeout(), transport=transport, headers=headers
        )

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
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            **self._extra_body,
        }
        if stop:
            payload["stop"] = stop
        pieces: list[str] = []
        try:
            with self._http.stream("POST", f"{self.base_url}/chat/completions", json=payload) as r:
                if r.status_code >= 400:
                    raise explain_http_error(r, self.name, self._status_hint(r.status_code))
                for line in r.iter_lines():
                    data = parse_sse_data(line)
                    if data is None:
                        continue
                    if data == "[DONE]":
                        break
                    piece = self._piece(data)
                    if piece:
                        pieces.append(piece)
                        if on_token is not None:
                            on_token(piece)
        except httpx.ConnectError as exc:
            raise unreachable(self.name, self.base_url, self._start_hint()) from exc
        except httpx.ConnectTimeout as exc:
            raise unreachable(self.name, self.base_url, self._start_hint()) from exc
        except httpx.ReadTimeout as exc:
            raise timed_out(self.name, self._http.timeout.read or 0) from exc
        except httpx.HTTPError as exc:
            raise ModelError(
                f"The connection to {self.name} at {self.base_url} failed ({exc}). "
                "Check that the server is still running and try again."
            ) from exc
        return "".join(pieces)

    def health(self) -> bool:
        try:
            return self._http.get(f"{self.base_url}/models", timeout=5.0).status_code == 200
        except httpx.HTTPError:
            return False

    def unload(self) -> None:
        """Nothing to do: the user runs this server."""

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------ helpers

    def _piece(self, data: str) -> str:
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            return ""
        if isinstance(event, dict) and "error" in event:
            err = event["error"]
            msg = err.get("message") if isinstance(err, dict) else err
            raise ModelError(f"{self.name} reported an error while answering: {msg}")
        choices = event.get("choices") if isinstance(event, dict) else None
        if not choices:
            return ""
        delta = choices[0].get("delta") or {}
        return delta.get("content") or ""

    def _start_hint(self) -> str:
        return (
            "Start the server (for LM Studio, load a model and start the local server) and check "
            f"that the address {self.base_url} is right."
        )

    def _status_hint(self, status: int) -> str:
        if status in (401, 403):
            return "The server wants an API key; check the key and try again."
        if status == 404:
            return "Check the base_url and the model name."
        return "Check the server window for details, then try again."
