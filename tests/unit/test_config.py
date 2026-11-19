from __future__ import annotations

import logging
from pathlib import Path

from qedcode import config
from qedcode.config import (
    DEFAULT_PROTECTED,
    GlobalConfig,
    ProjectConfig,
    global_config_path,
    load_global,
    load_project,
    qedcode_home,
    save_global,
    save_project,
)
from qedcode.models.schemas import Mode, Speed


def test_home_override_is_used(_isolated_qedcode_home: Path) -> None:
    assert qedcode_home() == _isolated_qedcode_home
    assert global_config_path() == _isolated_qedcode_home / "config.toml"


def test_missing_files_give_defaults(tmp_path: Path) -> None:
    assert load_global() == GlobalConfig()
    assert load_project(tmp_path) == ProjectConfig()
    assert not global_config_path().exists()  # loading never creates files


def test_global_round_trip_creates_folders_in_temp_only(_isolated_qedcode_home: Path) -> None:
    cfg = GlobalConfig()
    cfg.hardware.vendor = "nvidia"
    cfg.hardware.gpu_name = "NVIDIA GeForce RTX 5060 Ti"
    cfg.hardware.vram_gb = 16.0
    cfg.hardware.backend = "cuda"
    cfg.engine.mode = Mode.MIX
    cfg.engine.speed = Speed.SURE
    cfg.helper.enabled = True
    cfg.helper.model = "anthropic/some-model"
    path = save_global(cfg)
    assert path.is_relative_to(_isolated_qedcode_home)
    assert load_global() == cfg


def test_project_round_trip(tmp_path: Path) -> None:
    cfg = ProjectConfig(python=".venv/bin/python", confirmed=True, test_timeout_seconds=42)
    path = save_project(tmp_path, cfg)
    assert path == tmp_path / ".qed" / "project.toml"
    assert load_project(tmp_path) == cfg


def test_project_defaults_have_protected_paths(tmp_path: Path) -> None:
    cfg = load_project(tmp_path)
    assert cfg.protected == DEFAULT_PROTECTED
    assert cfg.test_command == ["{python}", "-m", "pytest"]
    assert cfg.confirmed is False
    # mutating one instance's list must not leak into another
    cfg.protected.append("extra/**")
    assert "extra/**" not in ProjectConfig().protected


def test_unknown_keys_are_ignored_with_a_warning(caplog, tmp_path: Path) -> None:
    path = global_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '[engine]\nmode = "cloud"\nturbo = true\n[mystery]\nx = 1\n',
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="qedcode.config"):
        cfg = load_global()
    assert cfg.engine.mode is Mode.CLOUD
    text = caplog.text
    assert "engine.turbo" in text
    assert "mystery" in text


def test_broken_toml_falls_back_to_defaults(caplog) -> None:
    path = global_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("this is = = not toml", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="qedcode.config"):
        assert load_global() == GlobalConfig()
    assert "defaults" in caplog.text


def test_invalid_values_fall_back_to_defaults(caplog) -> None:
    path = global_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[engine]\nspeed = "warp"\n', encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="qedcode.config"):
        assert load_global() == GlobalConfig()


def test_config_never_stores_secrets() -> None:
    forbidden = ("key", "token", "secret", "password", "credential")

    def walk(model_cls) -> list[str]:
        names = []
        for name, field in model_cls.model_fields.items():
            names.append(name)
            nested = config._nested_model(field.annotation)
            if nested is not None:
                names.extend(walk(nested))
        return names

    for model_cls in (GlobalConfig, ProjectConfig):
        for name in walk(model_cls):
            assert not any(word in name.lower() for word in forbidden), name


def test_a_secret_in_a_config_file_is_dropped_on_resave() -> None:
    path = global_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[cloud]\nmodel = "m"\napi_key = "sk-test-123"\n', encoding="utf-8")
    save_global(load_global())
    assert "sk-test-123" not in path.read_text(encoding="utf-8")
