"""GlobalConfig and ProjectConfig (AGENTS.md section 12) and their TOML load/save.

API keys are never stored here: they come from the OS keychain or environment variables.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Literal, TypeVar, get_args, get_origin

import tomli_w
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from qedcode.models.schemas import Mode, Speed

log = logging.getLogger("qedcode.config")

DEFAULT_PROTECTED = [
    "tests/**",
    "test/**",
    "**/test_*.py",
    "**/*_test.py",
    "**/conftest.py",
    "pytest.ini",
    "tox.ini",
    "setup.cfg",
    "pyproject.toml",
    "noxfile.py",
    ".github/**",
]

PROJECT_CONFIG_NAME = "project.toml"


class _Section(BaseModel):
    """Base for config sections: unknown keys are ignored (and reported by the loader)."""

    model_config = ConfigDict(extra="ignore")


class HardwareConfig(_Section):
    vendor: Literal["nvidia", "amd", "intel", "apple", "cpu"] = "cpu"
    gpu_name: str = ""
    vram_gb: float = 0.0
    backend: Literal["cuda", "vulkan", "rocm", "hip", "metal", "cpu"] = "cpu"


class EngineConfig(_Section):
    mode: Mode = Mode.LOCAL
    speed: Speed = Speed.NORMAL
    keep_alive_minutes: int = 10
    unload_on_exit: bool = True
    learn: bool = True  # remember applied A/B fixes and show similar ones to the local model


class LocalConfig(_Section):
    backend: Literal["llamacpp", "ollama", "openai_compat"] = "llamacpp"
    model: str = "qwen3.5-4b-q4km"  # catalog id, Ollama tag, or a path to a .gguf
    base_url: str = ""  # openai_compat only
    ctx: int = 16384
    kv_cache: Literal["f16", "q8_0"] = "q8_0"
    llamacpp_tag: str = ""  # pinned llama.cpp release tag; empty = latest


class HelperConfig(_Section):
    enabled: bool = False
    backend: Literal["cloud", "ollama", "openai_compat"] = "cloud"
    model: str = ""
    base_url: str = ""  # openai_compat helpers only
    ask_before_use: bool = True  # MCP and CLI runs never use the helper unless this is False


class CloudConfig(_Section):
    model: str = ""


class GlobalConfig(_Section):
    hardware: HardwareConfig = Field(default_factory=HardwareConfig)
    engine: EngineConfig = Field(default_factory=EngineConfig)
    local: LocalConfig = Field(default_factory=LocalConfig)
    helper: HelperConfig = Field(default_factory=HelperConfig)
    cloud: CloudConfig = Field(default_factory=CloudConfig)


class ProjectConfig(_Section):
    language: str = "python"
    python: str = ""  # the interpreter that runs this project's tests
    test_command: list[str] = Field(default_factory=lambda: ["{python}", "-m", "pytest"])
    test_timeout_seconds: int = 300
    protected: list[str] = Field(default_factory=lambda: list(DEFAULT_PROTECTED))
    confirmed: bool = False
    sandbox: Literal["off", "docker"] = "off"  # "docker": run tests in a sealed container
    sandbox_image: str = ""  # set by `qed sandbox setup`
    sandbox_memory: str = "4g"
    sandbox_cpus: float = 0.0  # 0 = no limit


# --------------------------------------------------------------------------- locations


def qedcode_home() -> Path:
    """Return the QEDCode home folder: `QEDCODE_HOME` if set, else `~/.qedcode`."""
    override = os.environ.get("QEDCODE_HOME")
    return Path(override).expanduser() if override else Path.home() / ".qedcode"


def global_config_path() -> Path:
    return qedcode_home() / "config.toml"


def logs_dir() -> Path:
    return qedcode_home() / "logs"


def models_dir() -> Path:
    return qedcode_home() / "models"


def bin_dir() -> Path:
    return qedcode_home() / "bin"


def run_dir() -> Path:
    return qedcode_home() / "run"


def project_config_path(root: Path) -> Path:
    return Path(root) / ".qed" / PROJECT_CONFIG_NAME


# --------------------------------------------------------------------------- load / save

M = TypeVar("M", bound=_Section)


def _nested_model(annotation: Any) -> type[BaseModel] | None:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    if get_origin(annotation) is not None:
        for arg in get_args(annotation):
            if isinstance(arg, type) and issubclass(arg, BaseModel):
                return arg
    return None


def unknown_keys(model_cls: type[BaseModel], data: dict[str, Any], prefix: str = "") -> list[str]:
    """List dotted names of keys in `data` that `model_cls` does not define."""
    found: list[str] = []
    for key, value in data.items():
        field = model_cls.model_fields.get(key)
        dotted = f"{prefix}{key}"
        if field is None:
            found.append(dotted)
            continue
        nested = _nested_model(field.annotation)
        if nested is not None and isinstance(value, dict):
            found.extend(unknown_keys(nested, value, f"{dotted}."))
    return found


def _load_toml(path: Path) -> dict[str, Any]:
    import tomllib

    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError:
        return {}
    except (tomllib.TOMLDecodeError, OSError) as exc:
        log.warning("Could not read %s (%s); using defaults.", path, exc)
        return {}


def _load_model(model_cls: type[M], path: Path) -> M:
    data = _load_toml(path)
    for key in unknown_keys(model_cls, data):
        log.warning("Ignoring unknown setting '%s' in %s", key, path)
    try:
        return model_cls.model_validate(data)
    except ValidationError as exc:
        log.warning("Invalid settings in %s (%s); using defaults.", path, exc.error_count())
        return model_cls()


def _save_model(cfg: BaseModel, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = cfg.model_dump(mode="json", exclude_none=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(tomli_w.dumps(data), encoding="utf-8")
    tmp.replace(path)
    return path


def load_global() -> GlobalConfig:
    """Load `~/.qedcode/config.toml`; a missing or broken file gives defaults."""
    return _load_model(GlobalConfig, global_config_path())


def save_global(cfg: GlobalConfig) -> Path:
    """Write the global config, creating `~/.qedcode` on first save."""
    return _save_model(cfg, global_config_path())


def load_project(root: Path) -> ProjectConfig:
    """Load `<root>/.qed/project.toml`; a missing or broken file gives defaults."""
    return _load_model(ProjectConfig, project_config_path(root))


def save_project(root: Path, cfg: ProjectConfig) -> Path:
    """Write the project config, creating `.qed/` on first save."""
    return _save_model(cfg, project_config_path(root))
