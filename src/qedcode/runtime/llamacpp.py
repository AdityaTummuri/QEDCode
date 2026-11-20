"""LlamaCppClient: a ModelClient over the shared llama-server (AGENTS.md section 10)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx

from qedcode.config import GlobalConfig
from qedcode.runtime.client import ModelError
from qedcode.runtime.openai_compat import OpenAICompatClient
from qedcode.runtime.quiet import is_quiet, quiet_threads
from qedcode.runtime.server_manager import ServerInfo, ServerManager, ServerSpec

# Qwen-style chat templates can think out loud; QEDCode wants only the formatted reply.
_NO_THINKING = {"chat_template_kwargs": {"enable_thinking": False}, "cache_prompt": True}


class LlamaCppClient(OpenAICompatClient):
    """Chat with the local llama-server, starting or reusing it on the first call (lazy)."""

    backend = "llamacpp"

    def __init__(
        self,
        spec: ServerSpec,
        manager: ServerManager | None = None,
        *,
        unload_on_exit: bool = True,
        on_status: Callable[[str], None] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(
            spec.model_id,
            "http://127.0.0.1:0/v1",
            ctx=spec.ctx,
            extra_body=_NO_THINKING,
            transport=transport,
        )
        self.name = f"{self.backend}:{spec.model_id}"
        self.spec = spec
        self.manager = manager or ServerManager()
        self.unload_on_exit = unload_on_exit
        self._on_status = on_status
        self.info: ServerInfo | None = None

    def ensure_started(self, on_status: Callable[[str], None] | None = None) -> ServerInfo:
        """Start (or reuse) the shared server and point this client at it."""
        info = self.manager.ensure(self.spec, on_status=on_status or self._on_status)
        self.info = info
        self.base_url = f"{info.base_url}/v1"
        self.context_tokens = info.ctx
        return info

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
        if self.info is None or not self.manager.is_healthy(self.info):
            self.ensure_started()
        self.manager.touch()
        return super().chat(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stop=stop,
            on_token=on_token,
            tag=tag,
        )

    def health(self) -> bool:
        return self.manager.is_healthy()

    def unload(self) -> None:
        """Unregister from the shared server and stop it when nobody else is using it."""
        self.manager.release(unload_on_exit=self.unload_on_exit)
        self.info = None


def resolve_model_path(model: str) -> tuple[str, Path]:
    """Turn config `local.model` (a catalog id or a .gguf path) into (id, file path)."""
    from qedcode.runtime.catalog import CatalogError, load_catalog, local_path

    candidate = Path(model).expanduser()
    if model.lower().endswith(".gguf"):
        if not candidate.is_file():
            raise ModelError(
                f"The model file {candidate} does not exist. Check [local] model in "
                "~/.qedcode/config.toml, or run `qed setup`."
            )
        return candidate.stem, candidate
    try:
        entry = load_catalog().require(model)
    except CatalogError as exc:
        raise ModelError(str(exc)) from exc
    path = local_path(entry)
    if not path.is_file():
        raise ModelError(
            f"The model '{model}' is not downloaded yet. Run `qed models download {model}` "
            "(it asks before downloading) or `qed setup`."
        )
    return entry.id, path


def make_llamacpp_client(config: GlobalConfig) -> LlamaCppClient:
    """Build the llama.cpp client from config. The server itself starts on the first chat."""
    from qedcode.hardware.detect import current_hardware
    from qedcode.runtime.installer import (
        DEFAULT_LLAMACPP_TAG,
        engine_env,
        find_installed_engine,
    )

    hw = current_hardware()
    quiet = is_quiet(config)
    tag = config.local.llamacpp_tag or DEFAULT_LLAMACPP_TAG
    engine = find_installed_engine(hw, tag)
    if engine is None:
        raise ModelError(
            f"The llama.cpp engine ({tag}) is not installed. Run `qed setup` (it asks before "
            "downloading anything), or use Ollama or LM Studio instead."
        )
    model_id, path = resolve_model_path(config.local.model)
    spec = ServerSpec(
        model_id=model_id,
        model_path=path,
        launcher=[str(engine.binary)],
        ctx=config.local.ctx,
        kv_cache=config.local.kv_cache,
        ngl=0 if hw.backend == "cpu" else 99,
        engine_tag=tag,
        cwd=engine.bin_dir,
        env=engine_env(engine),
        flags_cache=engine.directory / "flags.json",
        quiet=quiet,
        threads=quiet_threads() if quiet else 0,
    )
    return LlamaCppClient(spec, unload_on_exit=config.engine.unload_on_exit)
