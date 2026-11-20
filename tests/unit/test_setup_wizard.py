from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qedcode.cli import app
from qedcode.config import GlobalConfig, global_config_path, load_global, save_global
from qedcode.hardware.detect import HardwareInfo
from qedcode.models.schemas import Mode
from qedcode.runtime import installer
from qedcode.runtime.catalog import load_catalog
from qedcode.runtime.client import ModelError
from qedcode.runtime.installer import Asset, EnginePlan, InstalledEngine
from qedcode.runtime.secrets import SecretsError
from qedcode.setup_wizard import SetupDeps, run_setup
from qedcode.verify.run import RunResult

NVIDIA6 = HardwareInfo(
    vendor="nvidia", gpu_name="NVIDIA GeForce RTX 4050 Laptop GPU", vram_gb=6.0, backend="cuda"
)
DEFAULT = "<default>"


class ScriptedPrompter:
    """Answers the wizard's questions from a list, and fails if the wizard asks too much."""

    def __init__(self, answers: list[str | bool]) -> None:
        self.answers = list(answers)
        self.said: list[str] = []
        self.asked: list[tuple[str, str, object]] = []
        self.progress_calls: list[tuple[str, int, int]] = []
        self.hardware_shown: list[HardwareInfo] = []

    def _next(self, kind: str, question: str, default: object) -> object:
        assert self.answers, f"The wizard asked more than scripted: {kind}: {question!r}"
        answer = self.answers.pop(0)
        self.asked.append((kind, question, default))
        return default if answer == DEFAULT else answer

    def say(self, message: str) -> None:
        self.said.append(message)

    def show_hardware(self, info: HardwareInfo) -> None:
        self.hardware_shown.append(info)

    def choose(self, question, options, default=None):
        answer = self._next("choose", question, default)
        assert answer in [v for v, _ in options], f"{answer!r} is not an option of {question!r}"
        return answer

    def text(self, question, default=""):
        return self._next("text", question, default)

    def secret(self, question):
        return self._next("secret", question, "")

    def confirm(self, question, default=False):
        return self._next("confirm", question, default)

    def progress(self, name, done, total):
        self.progress_calls.append((name, done, total))

    @property
    def transcript(self) -> str:
        return "\n".join(self.said)


class FakeClient:
    name = "fake:client"
    context_tokens = 8192

    def __init__(self, reply="ready", pieces=10, error: Exception | None = None) -> None:
        self.reply, self.pieces, self.error = reply, pieces, error
        self.unloaded = False
        self.calls = []

    def chat(self, messages, *, temperature, max_tokens, stop=None, on_token=None, tag=""):
        self.calls.append((messages, temperature, max_tokens, tag))
        if self.error:
            raise self.error
        for _ in range(self.pieces):
            if on_token:
                on_token("x")
        return self.reply

    def health(self):
        return True

    def unload(self):
        self.unloaded = True


@dataclass
class Spy:
    plans: list = field(default_factory=list)
    engine_downloads: list = field(default_factory=list)
    model_downloads: list = field(default_factory=list)
    commands: list = field(default_factory=list)
    keys: dict = field(default_factory=dict)
    client: FakeClient = field(default_factory=FakeClient)
    made_with: list = field(default_factory=list)


def make_deps(
    spy: Spy,
    *,
    hw: HardwareInfo = NVIDIA6,
    ollama: list[str] | None = None,
    lmstudio: list[str] | None = None,
    engine_installed: bool = False,
    model_present: bool = False,
    claude: bool = False,
    release_error: Exception | None = None,
    claude_ok: bool = True,
) -> SetupDeps:
    plan = EnginePlan(
        "b1",
        "cuda",
        [
            Asset(
                "x.zip",
                "https://github.com/ggml-org/llama.cpp/releases/download/b1/x.zip",
                300_000_000,
            )
        ],
        "test",
    )
    clock = iter([0.0, 2.0] * 10)

    def fetch_release(tag):
        if release_error:
            raise release_error
        return {"tag_name": tag}

    def download_engine(plans, confirm, progress):
        spy.engine_downloads.append(plans)
        progress("engine", 1, 2)
        return InstalledEngine("b1", "cuda", Path("/x"), Path("/x/llama-server"))

    def download_model(entry, confirm, progress):
        spy.model_downloads.append(entry.id)
        progress(entry.gguf.file, 5, 5)
        return Path("/models") / entry.gguf.file

    def make_client(cfg):
        spy.made_with.append(cfg.model_copy(deep=True))
        return spy.client

    def run_command(args):
        spy.commands.append(args)
        return RunResult(
            args=args, exit_code=0 if claude_ok else 1, stderr="" if claude_ok else "boom"
        )

    return SetupDeps(
        detect_hardware=lambda: hw,
        probe_ollama=lambda: ollama,
        probe_lmstudio=lambda: lmstudio,
        fetch_release=fetch_release,
        find_plans=lambda hwi, release: [plan],
        installed_engine=lambda hwi, tag: (
            InstalledEngine("b1", "cuda", Path("/x"), Path("/x/ls")) if engine_installed else None
        ),
        download_engine=download_engine,
        download_model=download_model,
        model_present=lambda entry: model_present,
        make_client=make_client,
        which=lambda name: "/usr/bin/claude" if claude and name == "claude" else None,
        home=lambda: global_config_path().parent / "no-agents-here",  # never the real home
        run_command=run_command,
        get_key=lambda provider: spy.keys.get(provider),
        set_key=lambda provider, key: spy.keys.__setitem__(provider, key),
        clock=lambda: next(clock),
    )


# ------------------------------------------------------------------ the local path


def test_recommended_local_setup_end_to_end() -> None:
    spy = Spy()
    io = ScriptedPrompter([True, "local", "recommended", True])  # hw ok, mode, model, download
    result = run_setup(io, make_deps(spy))
    cfg = load_global()
    assert (cfg.engine.mode, cfg.local.backend, cfg.local.model) == (
        Mode.LOCAL,
        "llamacpp",
        "qwen3.5-4b-q4km",
    )
    assert cfg.local.ctx == 32768 and cfg.local.kv_cache == "q8_0"
    assert (cfg.hardware.vendor, cfg.hardware.vram_gb, cfg.hardware.backend) == (
        "nvidia",
        6.0,
        "cuda",
    )
    assert len(spy.engine_downloads) == 1 and spy.model_downloads == ["qwen3.5-4b-q4km"]
    assert io.progress_calls  # downloads reported progress through the prompter
    assert "To download:" in io.transcript and "llama.cpp engine (cuda build)" in io.transcript
    assert "Qwen3.5 4B" in io.transcript and "Total:" in io.transcript
    assert result.tokens_per_second == pytest.approx(5.0)  # 10 pieces in 2 seconds
    assert "5.0 tokens per second" in io.transcript
    assert spy.client.unloaded is True  # the GPU is freed after the test
    assert spy.client.calls[0][3] == "setup" and spy.client.calls[0][1] == 0.0
    assert spy.made_with[0].local.model == "qwen3.5-4b-q4km"  # config was saved before the test
    assert global_config_path().exists()
    assert not io.answers  # every scripted answer was used, nothing more was asked


def test_declining_the_downloads_saves_settings_but_downloads_nothing() -> None:
    spy = Spy()
    io = ScriptedPrompter([True, "local", "recommended", False])
    result = run_setup(io, make_deps(spy))
    assert spy.engine_downloads == [] and spy.model_downloads == []
    assert spy.made_with == []  # nothing to test
    assert result.tokens_per_second is None
    assert "Nothing was downloaded" in io.transcript
    assert load_global().local.model == "qwen3.5-4b-q4km"


def test_everything_already_installed_asks_nothing_about_downloads() -> None:
    spy = Spy()
    io = ScriptedPrompter([True, "local", "recommended"])
    run_setup(io, make_deps(spy, engine_installed=True, model_present=True))
    assert "already installed" in io.transcript
    assert spy.engine_downloads == [] and spy.model_downloads == []
    assert len(spy.made_with) == 1  # still tested


def test_only_the_missing_piece_is_listed() -> None:
    spy = Spy()
    io = ScriptedPrompter([True, "local", "recommended", True])
    run_setup(io, make_deps(spy, engine_installed=True, model_present=False))
    assert "llama.cpp engine" not in io.transcript
    assert spy.engine_downloads == [] and spy.model_downloads == ["qwen3.5-4b-q4km"]


def test_hardware_can_be_overridden_and_changes_the_recommendation() -> None:
    spy = Spy()
    io = ScriptedPrompter(
        [False, "nvidia", "My GPU", "4", "local", "recommended", False]
    )  # hw wrong -> vendor, name, VRAM
    run_setup(io, make_deps(spy))
    cfg = load_global()
    assert (cfg.hardware.vendor, cfg.hardware.gpu_name, cfg.hardware.vram_gb) == (
        "nvidia",
        "My GPU",
        4.0,
    )
    assert cfg.local.ctx == 12288  # what fits on 4 GB


def test_a_typo_in_vram_asks_again() -> None:
    spy = Spy()
    io = ScriptedPrompter([False, "amd", "", "lots", "8,5", "local", "skip"])
    run_setup(io, make_deps(spy))
    assert "Please type a number" in io.transcript
    assert load_global().hardware.vram_gb == 8.5


def test_choosing_another_catalog_model() -> None:
    spy = Spy()
    io = ScriptedPrompter([True, "local", "catalog", "qwen2.5-coder-7b-q4km", True])
    run_setup(io, make_deps(spy))
    assert load_global().local.model == "qwen2.5-coder-7b-q4km"
    assert spy.model_downloads == ["qwen2.5-coder-7b-q4km"]


def test_skip_leaves_the_model_alone() -> None:
    spy = Spy()
    io = ScriptedPrompter([True, "local", "skip"])
    result = run_setup(io, make_deps(spy))
    assert result.skip_test and spy.made_with == []
    assert global_config_path().exists()


# ------------------------------------------------------------------ models that already exist


def test_existing_ollama_model() -> None:
    spy = Spy()
    io = ScriptedPrompter([True, "local", "existing", "ollama", "llama3.2:3b"])
    run_setup(io, make_deps(spy, ollama=["qwen3.5:4b", "llama3.2:3b"]))
    cfg = load_global()
    assert (cfg.local.backend, cfg.local.model) == ("ollama", "llama3.2:3b")
    assert spy.engine_downloads == [] and spy.model_downloads == []
    assert len(spy.made_with) == 1


def test_existing_lm_studio_model() -> None:
    spy = Spy()
    io = ScriptedPrompter([True, "local", "existing", "lmstudio", "my-lm-model"])
    run_setup(io, make_deps(spy, lmstudio=["my-lm-model"]))
    cfg = load_global()
    assert (cfg.local.backend, cfg.local.model) == ("openai_compat", "my-lm-model")
    assert cfg.local.base_url == "http://127.0.0.1:1234/v1"


def test_gguf_path_is_checked_until_it_exists(tmp_path: Path) -> None:
    gguf = tmp_path / "mine.gguf"
    gguf.write_bytes(b"x")
    spy = Spy()
    io = ScriptedPrompter(
        [True, "local", "existing", "gguf", str(tmp_path / "nope.gguf"), str(gguf)]
    )
    run_setup(io, make_deps(spy))
    assert "not found" in io.transcript
    cfg = load_global()
    assert (cfg.local.backend, cfg.local.model) == ("llamacpp", str(gguf))


def test_ollama_tag_can_be_typed_when_ollama_is_not_running() -> None:
    spy = Spy()
    io = ScriptedPrompter([True, "local", "existing", "ollama_tag", "qwen3.5:4b"])
    run_setup(io, make_deps(spy, ollama=None))
    assert (load_global().local.backend, load_global().local.model) == ("ollama", "qwen3.5:4b")


# ------------------------------------------------------------------ mix and cloud


def test_mix_with_a_cloud_helper_saves_the_key_in_the_keychain() -> None:
    spy = Spy()
    io = ScriptedPrompter(
        [
            True,
            "mix",
            "skip",
            "cloud",
            "openrouter",
            "openrouter/anthropic/some-model",
            "sk-or-secret-12345",
        ]
    )
    run_setup(io, make_deps(spy))
    cfg = load_global()
    assert cfg.engine.mode is Mode.MIX
    assert (cfg.helper.enabled, cfg.helper.backend) == (True, "cloud")
    assert cfg.helper.model == "openrouter/anthropic/some-model"
    assert cfg.helper.ask_before_use is True
    assert spy.keys == {"openrouter": "sk-or-secret-12345"}
    assert "sk-or-secret-12345" not in global_config_path().read_text(encoding="utf-8")
    assert "sk-or-secret-12345" not in io.transcript
    assert "only after you confirm" in io.transcript


def test_mix_with_an_ollama_helper() -> None:
    spy = Spy()
    io = ScriptedPrompter([True, "mix", "skip", "ollama", "big:70b"])
    run_setup(io, make_deps(spy, ollama=["small:1b", "big:70b"]))
    cfg = load_global()
    assert (cfg.helper.backend, cfg.helper.model) == ("ollama", "big:70b")


def test_cloud_mode_skips_the_local_model_and_stores_no_key_in_config() -> None:
    spy = Spy()
    io = ScriptedPrompter(
        [True, "cloud", "anthropic", "anthropic/claude-sonnet-5-5", "sk-ant-secret-98765"]
    )
    result = run_setup(io, make_deps(spy))
    cfg = load_global()
    assert cfg.engine.mode is Mode.CLOUD and cfg.cloud.model == "anthropic/claude-sonnet-5-5"
    assert spy.keys == {"anthropic": "sk-ant-secret-98765"}
    assert spy.made_with == [] and result.tokens_per_second is None
    assert "sent to anthropic" in io.transcript
    assert "sk-ant-secret-98765" not in global_config_path().read_text(encoding="utf-8")


def test_an_existing_key_is_kept_unless_replacement_is_requested() -> None:
    spy = Spy(keys={"anthropic": "old-key-123456"})
    io = ScriptedPrompter([True, "cloud", "anthropic", "anthropic/m", False])
    run_setup(io, make_deps(spy))
    assert spy.keys == {"anthropic": "old-key-123456"}
    io2 = ScriptedPrompter([True, "cloud", "anthropic", "anthropic/m", True, "new-key-123456"])
    run_setup(io2, make_deps(spy))
    assert spy.keys == {"anthropic": "new-key-123456"}


def test_empty_key_and_keychain_failure_are_handled_gently() -> None:
    spy = Spy()
    io = ScriptedPrompter([True, "cloud", "openai", "gpt-4o", ""])
    run_setup(io, make_deps(spy))
    assert spy.keys == {} and "OPENAI_API_KEY" in io.transcript

    deps = make_deps(Spy())

    def broken(provider, key):
        raise SecretsError("keychain unavailable; set OPENAI_API_KEY instead")

    deps.set_key = broken
    io2 = ScriptedPrompter([True, "cloud", "openai", "gpt-4o", "sk-key-12345678"])
    run_setup(io2, deps)
    assert "keychain unavailable" in io2.transcript
    assert "sk-key-12345678" not in io2.transcript


def test_local_providers_ask_for_no_key() -> None:
    spy = Spy()
    io = ScriptedPrompter([True, "cloud", "other", "ollama/llama3"])
    run_setup(io, make_deps(spy))
    assert spy.keys == {} and not io.answers


# ------------------------------------------------------------------ failures, agents, re-runs


def test_release_lookup_failure_is_reported_and_setup_continues() -> None:
    spy = Spy()
    err = installer.InstallError("Could not reach GitHub. Check your internet connection.")
    io = ScriptedPrompter([True, "local", "recommended"])
    result = run_setup(io, make_deps(spy, release_error=err, model_present=True))
    assert "Could not reach GitHub" in io.transcript
    assert result.skip_test and global_config_path().exists()


def test_a_failing_test_prompt_is_reported_not_raised() -> None:
    spy = Spy(client=FakeClient(error=ModelError("The model server did not start. See the log.")))
    io = ScriptedPrompter([True, "local", "recommended", True])
    result = run_setup(io, make_deps(spy))
    assert "did not start" in io.transcript
    assert result.tokens_per_second is None
    assert spy.client.unloaded is True


def test_a_failing_download_is_reported() -> None:
    spy = Spy()
    deps = make_deps(spy)

    def bad(*args, **kwargs):
        raise installer.InstallError("Downloading stopped (offline).")

    deps.download_model = bad
    io = ScriptedPrompter([True, "local", "recommended", True])
    result = run_setup(io, deps)
    assert "Downloading stopped" in io.transcript and result.skip_test


def test_claude_code_connection_is_offered_only_when_claude_exists() -> None:
    spy = Spy()
    run_setup(ScriptedPrompter([True, "local", "skip"]), make_deps(spy, claude=False))
    assert spy.commands == []
    io = ScriptedPrompter([True, "local", "skip", True])
    run_setup(io, make_deps(spy, claude=True))
    assert spy.commands == [["claude", "mcp", "add", "qedcode", "--", "qed", "mcp"]]
    assert "Connected" in io.transcript
    for name in ("OpenCode", "Cline", "Cursor"):
        assert name in io.transcript
    assert '"command": ["qed", "mcp"]' in io.transcript  # OpenCode's documented shape
    assert '"mcpServers": {"qedcode": {"command": "qed", "args": ["mcp"]}}' in io.transcript
    assert "formats change" in io.transcript


def test_claude_code_connection_failure_and_decline() -> None:
    spy = Spy()
    io = ScriptedPrompter([True, "local", "skip", True])
    run_setup(io, make_deps(spy, claude=True, claude_ok=False))
    assert "did not work" in io.transcript
    spy2 = Spy()
    run_setup(ScriptedPrompter([True, "local", "skip", False]), make_deps(spy2, claude=True))
    assert spy2.commands == []


def test_rerun_uses_current_values_as_defaults_and_reset_ignores_them() -> None:
    cfg = GlobalConfig()
    cfg.engine.mode = Mode.MIX
    cfg.local.model = "qwen3.5-2b-q4km"
    cfg.hardware.vendor, cfg.hardware.vram_gb, cfg.hardware.backend = "nvidia", 6.0, "cuda"
    save_global(cfg)
    io = ScriptedPrompter([True, DEFAULT, "skip", "ollama", "x:1b"])
    run_setup(io, make_deps(Spy(), ollama=["x:1b"]))
    mode_default = next(d for k, q, d in io.asked if q == "Where should the model run?")
    assert mode_default == "mix"  # the saved mode is the default
    assert "already have settings" in io.transcript

    io2 = ScriptedPrompter([True, DEFAULT, "skip"])
    run_setup(io2, make_deps(Spy()), reset=True)
    assert next(d for k, q, d in io2.asked if q == "Where should the model run?") == "local"
    assert load_global().helper.enabled is False  # the old mix settings are gone


def test_saved_hardware_differing_from_detected_asks_which_to_keep() -> None:
    cfg = GlobalConfig()
    cfg.hardware.vendor, cfg.hardware.vram_gb, cfg.hardware.backend = "nvidia", 4.0, "cuda"
    save_global(cfg)
    io = ScriptedPrompter([True, True, "local", "skip"])  # keep saved, hw ok, mode, skip
    run_setup(io, make_deps(Spy(), hw=NVIDIA6))
    assert "Your saved settings say nvidia with 4.0 GB" in io.transcript
    assert load_global().hardware.vram_gb == 4.0


def test_the_catalog_in_the_wizard_matches_the_real_catalog() -> None:
    assert {e.id for e in load_catalog().entries} >= {"qwen3.5-4b-q4km", "qwen2.5-coder-7b-q4km"}


# ------------------------------------------------------------------ the command


def test_qed_setup_command_runs_the_wizard(monkeypatch) -> None:
    called = {}
    monkeypatch.setattr(
        "qedcode.setup_wizard.run_setup", lambda io, **kw: called.update(kw) or None
    )
    result = CliRunner().invoke(app, ["setup", "--reset"])
    assert result.exit_code == 0 and called == {"reset": True}


def test_qed_setup_can_be_cancelled(monkeypatch) -> None:
    def interrupted(io, **kw):
        raise KeyboardInterrupt

    monkeypatch.setattr("qedcode.setup_wizard.run_setup", interrupted)
    result = CliRunner().invoke(app, ["setup"])
    assert result.exit_code == 1 and "Setup cancelled" in result.output
