"""`qed setup`: the one-command global wizard (AGENTS.md sections 10, 11 and 12).

Hardware -> mode -> model (download list, asked once) -> test answer with tokens per second ->
helper or cloud model and key -> agent connection -> save. The wizard talks to the user only
through a `Prompter` and reaches everything else through `SetupDeps`, so tests drive it with
scripted answers and mocks.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx

from qedcode import agents_connect
from qedcode.config import GlobalConfig, global_config_path, load_global, save_global
from qedcode.hardware import detect as hw_detect
from qedcode.hardware.detect import HardwareInfo, info_from_config, with_overrides
from qedcode.hardware.recommend import Recommendation, best_ctx_for, recommend
from qedcode.models.schemas import Mode
from qedcode.runtime import installer
from qedcode.runtime.catalog import Catalog, CatalogEntry, load_catalog, local_path
from qedcode.runtime.client import ModelClient, ModelError, make_client, ollama_base_url
from qedcode.runtime.secrets import (
    SecretsError,
    env_var_for,
    get_api_key,
    needs_key,
    provider_of,
    set_api_key,
)
from qedcode.verify.run import RunResult, run

LMSTUDIO_URL = "http://127.0.0.1:1234/v1"
MCP_DOCS = {
    "OpenCode": "https://opencode.ai/docs/mcp-servers",
    "Cline": "https://docs.cline.bot/mcp/mcp-overview",
    "Cursor": "https://cursor.com/docs (search for 'MCP')",
}
# Ready-to-paste snippets in each tool's documented shape (checked 2026-11-20). These formats
# change between releases, so the setup prints the docs link next to every snippet.
MCP_SNIPPETS = {
    "OpenCode": '{"mcp": {"servers": {"qedcode": {"type": "local", "command": ["qed", "mcp"]}}}}',
    "Cline": '{"mcpServers": {"qedcode": {"command": "qed", "args": ["mcp"]}}}',
    "Cursor": '{"mcpServers": {"qedcode": {"command": "qed", "args": ["mcp"]}}}',
}
CLOUD_PROVIDERS = [
    ("openrouter", "OpenRouter (many models, one key)"),
    ("anthropic", "Anthropic"),
    ("openai", "OpenAI"),
    ("gemini", "Google Gemini"),
    ("other", "Another provider (type the LiteLLM model string)"),
]
TEST_PROMPT = [{"role": "user", "content": "Reply with the single word: ready"}]


class Prompter(Protocol):
    """How the wizard talks to the user (Rich in the terminal, scripted answers in tests)."""

    def say(self, message: str) -> None: ...
    def show_hardware(self, info: HardwareInfo) -> None: ...
    def choose(
        self, question: str, options: list[tuple[str, str]], default: str | None = None
    ) -> str: ...
    def text(self, question: str, default: str = "") -> str: ...
    def secret(self, question: str) -> str: ...
    def confirm(self, question: str, default: bool = False) -> bool: ...
    def progress(self, name: str, done: int, total: int) -> None: ...


@dataclass
class SetupDeps:
    """Everything the wizard reaches outside itself; replaced by fakes in tests."""

    detect_hardware: Callable[[], HardwareInfo] = hw_detect.detect
    load_catalog: Callable[[], Catalog] = load_catalog
    probe_ollama: Callable[[], list[str] | None] = lambda: _probe_ollama()
    probe_lmstudio: Callable[[], list[str] | None] = lambda: _probe_lmstudio()
    fetch_release: Callable[[str | None], dict] = lambda tag: installer.fetch_release(tag)
    find_plans: Callable[[HardwareInfo, dict], list[installer.EnginePlan]] = (
        installer.find_llamacpp_assets
    )
    installed_engine: Callable[[HardwareInfo, str], installer.InstalledEngine | None] = (
        installer.find_installed_engine
    )
    download_engine: Callable[..., installer.InstalledEngine] = installer.download_llamacpp
    download_model: Callable[..., Path] = installer.download_model
    model_present: Callable[[CatalogEntry], bool] = lambda e: local_path(e).is_file()
    make_client: Callable[[GlobalConfig], ModelClient] = make_client
    which: Callable[[str], str | None] = shutil.which
    home: Callable[[], Path] = Path.home
    run_command: Callable[[list[str]], RunResult] = lambda args: run(args, timeout=90)
    get_key: Callable[[str], str | None] = get_api_key
    set_key: Callable[[str, str], None] = set_api_key
    load_config: Callable[[], GlobalConfig] = load_global
    save_config: Callable[[GlobalConfig], Path] = save_global
    config_exists: Callable[[], bool] = lambda: global_config_path().exists()
    clock: Callable[[], float] = time.perf_counter


@dataclass
class SetupResult:
    config: GlobalConfig
    saved: bool = True
    notes: list[str] = field(default_factory=list)
    skip_test: bool = False  # True when there is nothing to test (skipped, declined or failed)
    tokens_per_second: float | None = None

    def problem(self, note: str) -> None:
        """Record something the user must fix, and skip the model test."""
        self.notes.append(note)
        self.skip_test = True


# --------------------------------------------------------------------------- probes


def _probe_ollama() -> list[str] | None:
    from qedcode.runtime.ollama import OllamaClient

    client = OllamaClient("probe", base_url=ollama_base_url())
    try:
        if not client.is_running():
            return None
        return client.list_models()
    except ModelError:
        return None
    finally:
        client.close()


def _probe_lmstudio() -> list[str] | None:
    try:
        response = httpx.get(f"{LMSTUDIO_URL}/models", timeout=2.0)
        if response.status_code != 200:
            return None
        return [str(m.get("id")) for m in response.json().get("data", []) if m.get("id")]
    except (httpx.HTTPError, ValueError):
        return None


# --------------------------------------------------------------------------- the wizard


def run_setup(io: Prompter, deps: SetupDeps | None = None, *, reset: bool = False) -> SetupResult:
    """Run the wizard and save the config. Re-running shows current values as defaults."""
    deps = deps or SetupDeps()
    existing = deps.config_exists() and not reset
    cfg = deps.load_config() if existing else GlobalConfig()
    result = SetupResult(config=cfg)
    io.say("QEDCode setup. Nothing is downloaded or installed until you say yes.")
    if existing:
        io.say(
            "You already have settings; they are shown as the defaults. Use --reset to start over."
        )
    hw = _step_hardware(io, deps, cfg, existing)
    cfg.hardware = hw.to_config()
    cfg.engine.mode = Mode(
        io.choose(
            "Where should the model run?",
            [
                ("local", "local: everything stays on this computer"),
                ("mix", "mix: local first, a stronger helper model when stuck"),
                ("cloud", "cloud: a hosted model (code is sent to the provider)"),
            ],
            default=cfg.engine.mode.value,
        )
    )
    if cfg.engine.mode is not Mode.CLOUD:
        _step_local_model(io, deps, cfg, hw, result)
    deps.save_config(cfg)
    if cfg.engine.mode is not Mode.CLOUD:
        result.tokens_per_second = _step_test_model(io, deps, cfg, result)
    if cfg.engine.mode is Mode.MIX:
        _step_helper(io, deps, cfg)
    if cfg.engine.mode is Mode.CLOUD:
        _step_cloud(io, deps, cfg)
    _step_agents(io, deps)
    deps.save_config(cfg)
    io.say(
        f"Saved your settings to {global_config_path()}. Run `qed` in a project folder to start."
    )
    return result


# ----- step 1: hardware


def _step_hardware(
    io: Prompter, deps: SetupDeps, cfg: GlobalConfig, existing: bool
) -> HardwareInfo:
    detected = deps.detect_hardware()
    io.show_hardware(detected)
    hw = detected
    saved = info_from_config(cfg.hardware)
    if existing and (saved.vendor, saved.vram_gb) != (detected.vendor, detected.vram_gb):
        io.say(
            f"Your saved settings say {saved.vendor} with {saved.vram_gb:.1f} GB, "
            f"but this time {detected.vendor} with {detected.vram_gb:.1f} GB was detected."
        )
        if io.confirm("Keep your saved hardware settings?", default=True):
            hw = saved
    if io.confirm("Is this hardware right?", default=True):
        return hw
    vendor = io.choose(
        "Which graphics hardware do you have?",
        [
            ("nvidia", "NVIDIA"),
            ("amd", "AMD"),
            ("intel", "Intel Arc (discrete)"),
            ("apple", "Apple Silicon"),
            ("cpu", "None / CPU only"),
        ],
        default=hw.vendor,
    )
    name = io.text("GPU name (optional)", default=hw.gpu_name) if vendor != "cpu" else ""
    vram = 0.0
    if vendor != "cpu":
        vram = _ask_float(io, "How many GB of VRAM can QEDCode plan with?", hw.vram_gb or 6.0)
    return with_overrides(
        hw,
        vendor=vendor,  # type: ignore[arg-type]
        gpu_name=name,
        vram_gb=vram,
        system=hw_detect.sys_platform_name(),
    )


def _ask_float(io: Prompter, question: str, default: float) -> float:
    while True:
        raw = io.text(question, default=f"{default:g}")
        try:
            value = float(raw.replace(",", "."))
            if value >= 0:
                return value
        except ValueError:
            pass
        io.say("Please type a number such as 6 or 8.5.")


# ----- step 3: the local model


def _step_local_model(
    io: Prompter, deps: SetupDeps, cfg: GlobalConfig, hw: HardwareInfo, result: SetupResult
) -> None:
    catalog = deps.load_catalog()
    rec = recommend(hw, catalog)
    ollama, lmstudio = deps.probe_ollama(), deps.probe_lmstudio()
    io.say(
        f"Ollama: {'running' if ollama is not None else 'not found'}. "
        f"LM Studio: {'running' if lmstudio is not None else 'not found'}."
    )
    options = []
    if rec.model_id:
        entry = catalog.require(rec.model_id)
        options.append(
            (
                "recommended",
                f"The recommended model: {rec.display} "
                f"({_size(entry)}, {rec.ctx}-token context) through llama.cpp",
            )
        )
    options += [
        ("catalog", "Another model from the catalog"),
        ("existing", "A model I already run in Ollama or LM Studio, or a .gguf file"),
        ("skip", "Skip for now"),
    ]
    default = "recommended" if rec.model_id else "catalog"
    choice = io.choose("Which model should QEDCode use?", options, default=default)
    if choice == "skip":
        result.problem("Model setup skipped.")
        return
    if choice == "existing":
        _choose_existing(io, cfg, ollama, lmstudio)
        return
    entry = (
        _pick_catalog_entry(io, catalog, rec, hw)
        if choice == "catalog"
        else catalog.require(rec.model_id)
    )
    _use_catalog_model(io, deps, cfg, hw, entry, rec if entry.id == rec.model_id else None, result)


def _pick_catalog_entry(
    io: Prompter, catalog: Catalog, rec: Recommendation, hw: HardwareInfo
) -> CatalogEntry:
    options = []
    for entry in catalog.by_rank():
        fits = best_ctx_for(hw, entry) is not None
        note = "fits" if fits else "may not fit your GPU"
        options.append((entry.id, f"{entry.display}, {_size(entry)} ({note})"))
    return catalog.require(io.choose("Which catalog model?", options, default=rec.model_id or None))


def _choose_existing(
    io: Prompter, cfg: GlobalConfig, ollama: list[str] | None, lmstudio: list[str] | None
) -> None:
    kinds = []
    if ollama is not None:
        kinds.append(("ollama", f"A model in Ollama ({len(ollama)} found)"))
    if lmstudio is not None:
        kinds.append(("lmstudio", f"The model loaded in LM Studio ({len(lmstudio)} found)"))
    kinds.append(("gguf", "A .gguf file on this computer"))
    kinds.append(("ollama_tag", "Type an Ollama model tag (Ollama is started later)"))
    kind = io.choose("What do you already have?", kinds, default=kinds[0][0])
    if kind == "ollama" and ollama:
        cfg.local.backend, cfg.local.model = (
            "ollama",
            io.choose("Which Ollama model?", [(m, m) for m in ollama], default=ollama[0]),
        )
    elif kind == "ollama_tag" or kind == "ollama":
        cfg.local.backend = "ollama"
        cfg.local.model = io.text(
            "Ollama model tag (for example qwen3.5:4b)", default=cfg.local.model
        )
    elif kind == "lmstudio" and lmstudio:
        cfg.local.backend, cfg.local.base_url = "openai_compat", LMSTUDIO_URL
        cfg.local.model = io.choose(
            "Which LM Studio model?", [(m, m) for m in lmstudio], default=lmstudio[0]
        )
    else:
        while True:
            path = io.text("Path to the .gguf file").strip().strip('"')
            if path.lower().endswith(".gguf") and Path(path).expanduser().is_file():
                cfg.local.backend, cfg.local.model = "llamacpp", str(Path(path).expanduser())
                break
            io.say("That file was not found or is not a .gguf file. Check the path and try again.")


def _size(entry: CatalogEntry) -> str:
    size = entry.gguf.size_bytes or int((entry.weights_gb or 0) * 1e9)
    return installer.format_size(size)


def _use_catalog_model(
    io: Prompter,
    deps: SetupDeps,
    cfg: GlobalConfig,
    hw: HardwareInfo,
    entry: CatalogEntry,
    rec: Recommendation | None,
    result: SetupResult,
) -> None:
    cfg.local.backend, cfg.local.model = "llamacpp", entry.id
    ctx = rec.ctx if rec else best_ctx_for(hw, entry)
    if ctx:
        cfg.local.ctx = ctx
    elif hw.vram_gb > 0:
        io.say(
            f"Warning: {entry.display} may not fit in {hw.vram_gb:.1f} GB; it may run on the CPU."
        )
    cfg.local.kv_cache = "q8_0" if hw.vendor != "cpu" else "f16"
    tag = cfg.local.llamacpp_tag or installer.DEFAULT_LLAMACPP_TAG
    todo: list[tuple[str, int]] = []
    plans: list[installer.EnginePlan] = []
    if deps.installed_engine(hw, tag) is None:
        try:
            plans = deps.find_plans(hw, deps.fetch_release(tag))
        except installer.InstallError as exc:
            io.say(str(exc))
            result.problem("The engine could not be looked up; see the message above.")
        if plans:
            todo.append((f"llama.cpp engine ({plans[0].backend} build)", plans[0].total_bytes))
        elif not result.skip_test:
            io.say(
                "No llama.cpp build matches this computer. Ollama or LM Studio can be used instead."
            )
            result.problem("No llama.cpp build matches this computer.")
    need_model = not deps.model_present(entry)
    if need_model:
        todo.append((entry.display, entry.gguf.size_bytes or int((entry.weights_gb or 0) * 1e9)))
    if not todo:
        io.say("The engine and the model are already installed.")
        return
    total = sum(size for _, size in todo)
    io.say(
        "To download:\n"
        + "\n".join(f"  - {name}: {installer.format_size(size)}" for name, size in todo)
        + f"\n  Total: {installer.format_size(total)}"
    )
    if not io.confirm("Download and install these now?", default=True):
        result.problem("Downloads declined; run `qed setup` again when you are ready.")
        io.say("Nothing was downloaded. Run `qed setup` again when you are ready.")
        return
    try:
        if plans:
            deps.download_engine(plans, lambda _prompt: True, io.progress)
        if need_model:
            deps.download_model(entry, lambda _prompt: True, io.progress)
    except installer.InstallError as exc:
        io.say(str(exc))
        result.problem("A download failed; fix the problem above and run `qed setup` again.")


# ----- step 4: start the model and print tokens per second


def _step_test_model(
    io: Prompter, deps: SetupDeps, cfg: GlobalConfig, result: SetupResult
) -> float | None:
    if result.skip_test:
        return None
    try:
        client = deps.make_client(cfg)
    except ModelError as exc:
        io.say(str(exc))
        return None
    io.say("Loading the model and sending a one-line test prompt...")
    pieces = 0

    def count(_piece: str) -> None:
        nonlocal pieces
        pieces += 1

    started = deps.clock()
    try:
        reply = client.chat(
            TEST_PROMPT, temperature=0.0, max_tokens=32, on_token=count, tag="setup"
        )
    except ModelError as exc:
        io.say(str(exc))
        result.notes.append("The test prompt failed; see the message above.")
        return None
    finally:
        try:
            client.unload()
        except Exception:  # noqa: BLE001 - unloading is best effort
            pass
    elapsed = max(deps.clock() - started, 1e-6)
    rate = pieces / elapsed if pieces else 0.0
    io.say(f"The model answered: {reply.strip()[:80]!r}  ({rate:.1f} tokens per second)")
    return rate


# ----- step 5: helper and cloud


def _step_helper(io: Prompter, deps: SetupDeps, cfg: GlobalConfig) -> None:
    kind = io.choose(
        "Which helper should QEDCode ask when the local model is stuck?",
        [
            ("cloud", "A cloud model (needs an API key; code is sent to the provider)"),
            ("ollama", "A bigger model in Ollama (stays on this computer)"),
        ],
        default="cloud" if cfg.helper.backend == "cloud" else "ollama",
    )
    cfg.helper.enabled = True
    cfg.helper.backend = kind  # type: ignore[assignment]
    cfg.helper.ask_before_use = True
    if kind == "ollama":
        models = deps.probe_ollama() or []
        if models:
            cfg.helper.model = io.choose(
                "Which Ollama model?", [(m, m) for m in models], default=models[0]
            )
        else:
            cfg.helper.model = io.text("Ollama model tag for the helper", default=cfg.helper.model)
        return
    cfg.helper.model = _ask_cloud_model(io, deps, cfg.helper.model)
    io.say(
        "Note: in mix mode, parts of your code go to this cloud model, and only after you confirm."
    )


def _step_cloud(io: Prompter, deps: SetupDeps, cfg: GlobalConfig) -> None:
    cfg.cloud.model = _ask_cloud_model(io, deps, cfg.cloud.model)
    io.say(f"Note: in cloud mode, parts of your code are sent to {provider_of(cfg.cloud.model)}.")


def _ask_cloud_model(io: Prompter, deps: SetupDeps, current: str) -> str:
    current_provider = provider_of(current) if current else "openrouter"
    provider = io.choose(
        "Which provider?",
        CLOUD_PROVIDERS,
        default=current_provider if current_provider in dict(CLOUD_PROVIDERS) else "other",
    )
    hint = f"{provider}/<model name>" if provider != "other" else "provider/<model name>"
    model = ""
    while not model:
        model = io.text(f"Model string for LiteLLM (for example {hint})", default=current).strip()
        if not model:
            io.say("A model string is needed, for example anthropic/claude-sonnet-5-5.")
    chosen = provider_of(model)
    if needs_key(chosen):
        _ask_key(io, deps, chosen)
    return model


def _ask_key(io: Prompter, deps: SetupDeps, provider: str) -> None:
    if deps.get_key(provider):
        if not io.confirm(f"A key for {provider} is already saved. Replace it?", default=False):
            return
    io.say(
        f"Your key is stored in your system keychain, never in a file. "
        f"(You can also set {env_var_for(provider)} instead.)"
    )
    key = io.secret(f"API key for {provider} (input is hidden; leave empty to skip)").strip()
    if not key:
        io.say(f"No key saved. Set {env_var_for(provider)} before using this model.")
        return
    try:
        deps.set_key(provider, key)
        io.say("Key saved.")
    except SecretsError as exc:
        io.say(str(exc))


# ----- step 6: connect agents


def _step_agents(io: Prompter, deps: SetupDeps) -> None:
    env = agents_connect.Environment(
        home=deps.home(), which=deps.which, run_command=deps.run_command
    )
    left: list[str] = []
    for status in agents_connect.detect_agents(env):
        if status.connected:
            io.say(f"{status.name} is already connected to QEDCode.")
            continue
        if not status.detected:
            left.append(status.name)
            continue
        result = agents_connect.connect(status.key, lambda q: io.confirm(q, default=True), env)
        io.say(result.message)
        if not result.ok:
            left.append(status.name)
    pending = [name for name in MCP_DOCS if name in left]
    if pending:
        io.say(
            "Other agents: add QEDCode as an MCP server (command `qed`, argument `mcp`), or run "
            "`qed connect` later. Their config formats change, so check each tool's docs if a "
            "snippet does not work:"
        )
        for name in pending:
            io.say(f"  {name}: {MCP_DOCS[name]}")
            io.say(f"    {MCP_SNIPPETS[name]}")


# --------------------------------------------------------------------------- terminal prompter


class RichPrompter:
    """The real Prompter: Rich output and prompts in the terminal."""

    def __init__(self, console=None) -> None:
        from rich.console import Console

        self.console = console or Console()
        self._bar = None
        self._task = None

    def say(self, message: str) -> None:
        self._stop_bar()
        self.console.print(message, markup=False, highlight=False)

    def show_hardware(self, info: HardwareInfo) -> None:
        from qedcode.cli import hardware_table

        self.console.print(hardware_table(info))

    def choose(
        self, question: str, options: list[tuple[str, str]], default: str | None = None
    ) -> str:
        from rich.prompt import Prompt

        self._stop_bar()
        self.console.print(f"\n[bold]{question}[/bold]", highlight=False)
        values = [value for value, _ in options]
        for index, (_, label) in enumerate(options, start=1):
            self.console.print(f"  {index}) {label}", markup=False, highlight=False)
        default_index = str(values.index(default) + 1) if default in values else "1"
        answer = Prompt.ask(
            "Your choice",
            choices=[str(i) for i in range(1, len(options) + 1)],
            default=default_index,
        )
        return values[int(answer) - 1]

    def text(self, question: str, default: str = "") -> str:
        from rich.prompt import Prompt

        self._stop_bar()
        return Prompt.ask(question, default=default, show_default=bool(default))

    def secret(self, question: str) -> str:
        from rich.prompt import Prompt

        self._stop_bar()
        return Prompt.ask(question, password=True, default="", show_default=False)

    def confirm(self, question: str, default: bool = False) -> bool:
        from rich.prompt import Confirm

        self._stop_bar()
        return Confirm.ask(question, default=default)

    def progress(self, name: str, done: int, total: int) -> None:
        from rich.progress import (
            BarColumn,
            DownloadColumn,
            Progress,
            TextColumn,
            TimeRemainingColumn,
            TransferSpeedColumn,
        )

        if self._bar is None or self._task_name != name:
            self._stop_bar()
            self._bar = Progress(
                TextColumn("{task.description}"),
                BarColumn(),
                DownloadColumn(),
                TransferSpeedColumn(),
                TimeRemainingColumn(),
                console=self.console,
            )
            self._bar.start()
            self._task = self._bar.add_task(name, total=total or None)
            self._task_name = name
        self._bar.update(self._task, completed=done, total=total or None)

    _task_name = ""

    def _stop_bar(self) -> None:
        if self._bar is not None:
            self._bar.stop()
            self._bar = None
