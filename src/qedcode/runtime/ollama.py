"""OllamaClient: chat, model listing, pulling and unloading through a running Ollama."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx

from qedcode.runtime.client import (
    DEFAULT_OLLAMA_URL,
    ModelError,
    ServerNotRunning,
    default_timeout,
)
from qedcode.runtime.openai_compat import explain_http_error, timed_out

START_HINT = "Start Ollama (open the Ollama app, or run `ollama serve`) and try again."


class OllamaClient:
    """A ModelClient for Ollama's native /api/chat (streaming NDJSON)."""

    backend = "ollama"

    def __init__(
        self,
        model: str,
        base_url: str = DEFAULT_OLLAMA_URL,
        *,
        ctx: int = 16384,
        keep_alive_minutes: int = 10,
        transport: httpx.BaseTransport | None = None,
        timeout: httpx.Timeout | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.context_tokens = ctx
        self.keep_alive = f"{keep_alive_minutes}m"
        self.name = f"{self.backend}:{model}"
        self._http = httpx.Client(timeout=timeout or default_timeout(), transport=transport)

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
        options: dict[str, Any] = {
            "num_ctx": self.context_tokens,
            "temperature": temperature,
            "num_predict": max_tokens,
        }
        if stop:
            options["stop"] = stop
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "think": False,
            "options": options,
            "keep_alive": self.keep_alive,
        }
        pieces: list[str] = []
        try:
            with self._http.stream("POST", f"{self.base_url}/api/chat", json=payload) as r:
                if r.status_code >= 400:
                    raise explain_http_error(r, self.name, self._status_hint(r.status_code))
                for line in r.iter_lines():
                    if not line.strip():
                        continue
                    event = self._parse(line)
                    piece = (event.get("message") or {}).get("content") or ""
                    if piece:
                        pieces.append(piece)
                        if on_token is not None:
                            on_token(piece)
                    if event.get("done"):
                        break
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ServerNotRunning(
                f"QEDCode could not reach Ollama at {self.base_url}, so no model answered. "
                + START_HINT
            ) from exc
        except httpx.ReadTimeout as exc:
            raise timed_out(self.name, self._http.timeout.read or 0) from exc
        except httpx.HTTPError as exc:
            raise ModelError(
                f"The connection to Ollama at {self.base_url} failed ({exc}). "
                "Check that Ollama is still running and try again."
            ) from exc
        return "".join(pieces)

    def health(self) -> bool:
        return self.is_running()

    def unload(self) -> None:
        """Ask Ollama to drop the model from memory now (keep_alive 0)."""
        try:
            self._http.post(
                f"{self.base_url}/api/chat",
                json={"model": self.model, "messages": [], "keep_alive": 0},
                timeout=10.0,
            )
        except httpx.HTTPError:
            pass  # unloading is best effort; Ollama also unloads by itself when idle

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------ Ollama extras

    def is_running(self) -> bool:
        """True when an Ollama server answers at the base URL."""
        try:
            return self._http.get(f"{self.base_url}/api/tags", timeout=5.0).status_code == 200
        except httpx.HTTPError:
            return False

    def list_models(self) -> list[str]:
        """Names of the models Ollama has downloaded (GET /api/tags)."""
        try:
            response = self._http.get(f"{self.base_url}/api/tags", timeout=10.0)
        except httpx.HTTPError as exc:
            raise ServerNotRunning(
                f"QEDCode could not reach Ollama at {self.base_url} to list models. " + START_HINT
            ) from exc
        if response.status_code >= 400:
            raise explain_http_error(response, "Ollama", "")
        data = response.json()
        return [str(m.get("name")) for m in data.get("models", []) if m.get("name")]

    def pull(
        self,
        tag: str,
        on_progress: Callable[[str, int, int], None] | None = None,
    ) -> None:
        """Download a model through Ollama. on_progress(status, completed_bytes, total_bytes)."""
        try:
            with self._http.stream(
                "POST",
                f"{self.base_url}/api/pull",
                json={"model": tag, "stream": True},
                timeout=httpx.Timeout(connect=5.0, read=600.0, write=30.0, pool=5.0),
            ) as r:
                if r.status_code >= 400:
                    raise explain_http_error(r, "Ollama", f"Check the model tag '{tag}'.")
                for line in r.iter_lines():
                    if not line.strip():
                        continue
                    event = self._parse(line)
                    if on_progress is not None:
                        on_progress(
                            str(event.get("status", "")),
                            int(event.get("completed") or 0),
                            int(event.get("total") or 0),
                        )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ServerNotRunning(
                f"QEDCode could not reach Ollama at {self.base_url} to download '{tag}'. "
                + START_HINT
            ) from exc
        except httpx.HTTPError as exc:
            raise ModelError(
                f"The download of '{tag}' stopped ({exc}). Check your internet connection and "
                "run the command again; Ollama resumes where it stopped."
            ) from exc

    # ------------------------------------------------------------------ helpers

    def _parse(self, line: str) -> dict[str, Any]:
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ModelError(
                f"Ollama sent something QEDCode could not read: {line[:120]!r}. "
                "Update Ollama and try again."
            ) from exc
        if isinstance(event, dict) and event.get("error"):
            message = str(event["error"])
            hint = ""
            if "not found" in message:
                hint = f" Download it with `ollama pull {self.model}` or `qed models`."
            raise ModelError(f"Ollama reported an error: {message}.{hint}")
        return event if isinstance(event, dict) else {}

    def _status_hint(self, status: int) -> str:
        if status == 404:
            return (
                f"The model '{self.model}' may not be downloaded: run `ollama pull {self.model}`."
            )
        return "Check the Ollama window or logs for details, then try again."
