from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qedcode.cli import app
from qedcode.config import load_global, models_dir, save_global
from qedcode.hardware.detect import HardwareInfo
from qedcode.hardware.recommend import best_ctx_for
from qedcode.runtime import installer
from qedcode.runtime.catalog import Catalog, CatalogEntry, GgufSource, load_catalog, local_path
from qedcode.runtime.installer import (
    InstallDeclined,
    InstallError,
    download_model,
    remove_model,
    sha256_of,
    verify_model_file,
)

PAYLOAD = b"GGUF" + b"x" * 5000


def tiny_entry(payload: bytes = PAYLOAD, **overrides) -> CatalogEntry:
    data = dict(
        id="tiny-q4km",
        display="Tiny Model (Q4_K_M)",
        gguf=GgufSource(
            repo="someone/Tiny-GGUF",
            file="Tiny-Q4_K_M.gguf",
            revision="abc123",
            size_bytes=len(payload),
            size_gb=round(len(payload) / 1e9, 4),
            sha256=hashlib.sha256(payload).hexdigest(),
        ),
        kv_bytes_per_token_f16=1000,
        rank=1,
    )
    data.update(overrides)
    return CatalogEntry(**data)


class FakeHub:
    """Stands in for huggingface_hub.hf_hub_download: writes a file, never touches the network."""

    def __init__(self, payload: bytes = PAYLOAD, fail_first: bool = False) -> None:
        self.payload, self.fail_first, self.calls = payload, fail_first, []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail_first and len(self.calls) == 1:
            raise ConnectionError("network dropped")
        target = Path(kwargs["local_dir"]) / kwargs["filename"]
        target.parent.mkdir(parents=True, exist_ok=True)
        bar = kwargs["tqdm_class"](total=len(self.payload))
        half = len(self.payload) // 2
        bar.update(half)
        bar.update(len(self.payload) - half)
        target.write_bytes(self.payload)
        return str(target)


@pytest.fixture
def hub(monkeypatch):
    fake = FakeHub()
    monkeypatch.setattr(installer, "hf_hub_download", fake)
    return fake


def yes(prompt: str) -> bool:
    return True


# ------------------------------------------------------------------ download_model


def test_download_asks_with_the_size_then_downloads_and_verifies(hub) -> None:
    entry = tiny_entry()
    prompts: list[str] = []
    progress: list[tuple[str, int, int]] = []
    path = download_model(
        entry, lambda p: prompts.append(p) or True, lambda n, d, t: progress.append((n, d, t))
    )
    assert path == local_path(entry) == models_dir() / "tiny-q4km" / "Tiny-Q4_K_M.gguf"
    assert path.read_bytes() == PAYLOAD
    assert "Tiny Model" in prompts[0] and "huggingface.co/someone/Tiny-GGUF" in prompts[0]
    assert "MB" in prompts[0]
    call = hub.calls[0]
    assert call["repo_id"] == "someone/Tiny-GGUF"
    assert call["filename"] == "Tiny-Q4_K_M.gguf"
    assert call["revision"] == "abc123"
    assert Path(call["local_dir"]) == models_dir() / "tiny-q4km"
    assert progress[-1] == ("Tiny-Q4_K_M.gguf", len(PAYLOAD), len(PAYLOAD))
    assert verify_model_file(entry)


def test_declining_downloads_nothing(hub) -> None:
    with pytest.raises(InstallDeclined) as err:
        download_model(tiny_entry(), lambda p: False)
    assert "qed models download tiny-q4km" in str(err.value)
    assert hub.calls == []
    assert not (models_dir() / "tiny-q4km").exists()


def test_an_intact_existing_file_is_not_downloaded_again(hub) -> None:
    entry = tiny_entry()
    download_model(entry, yes)
    asked: list[str] = []
    again = download_model(entry, lambda p: asked.append(p) or True)
    assert again == local_path(entry)
    assert len(hub.calls) == 1 and asked == []


def test_a_corrupt_existing_file_is_downloaded_again(hub) -> None:
    entry = tiny_entry()
    target = local_path(entry)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"truncated")
    assert not verify_model_file(entry)
    download_model(entry, yes)
    assert verify_model_file(entry) and len(hub.calls) == 1


def test_checksum_mismatch_deletes_the_file_and_explains(monkeypatch) -> None:
    entry = tiny_entry()
    monkeypatch.setattr(installer, "hf_hub_download", FakeHub(payload=b"x" * len(PAYLOAD)))
    with pytest.raises(InstallError) as err:
        download_model(entry, yes)
    assert "checksum" in str(err.value) and "deleted" in str(err.value)
    assert not local_path(entry).exists()


def test_unverified_entries_are_refused(hub) -> None:
    entry = tiny_entry()
    entry.gguf.sha256 = "<fill>"
    with pytest.raises(InstallError) as err:
        download_model(entry, yes)
    assert "unverified" in str(err.value) and "gguf.sha256" in str(err.value)
    assert hub.calls == []


def test_network_errors_are_plain_english_and_a_retry_resumes(monkeypatch) -> None:
    flaky = FakeHub(fail_first=True)
    monkeypatch.setattr(installer, "hf_hub_download", flaky)
    entry = tiny_entry()
    with pytest.raises(InstallError) as err:
        download_model(entry, yes)
    assert "internet connection" in str(err.value)
    assert "resumes" in str(err.value)
    download_model(entry, yes)  # the retry succeeds
    assert verify_model_file(entry) and len(flaky.calls) == 2


def test_not_enough_disk_space_is_reported_before_downloading(hub, monkeypatch) -> None:
    monkeypatch.setattr(shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(10_000, 9_990, 10))
    with pytest.raises(InstallError) as err:
        download_model(tiny_entry(), yes)
    assert "free disk space" in str(err.value)
    assert hub.calls == []


def test_sha256_of_matches_hashlib(tmp_path: Path) -> None:
    f = tmp_path / "f.bin"
    f.write_bytes(PAYLOAD * 300)
    assert sha256_of(f, chunk=777) == hashlib.sha256(PAYLOAD * 300).hexdigest()


def test_verify_without_a_listed_checksum_checks_size_only() -> None:
    entry = tiny_entry()
    entry.gguf.sha256 = "<fill>"
    path = local_path(entry)
    path.parent.mkdir(parents=True)
    path.write_bytes(PAYLOAD)
    assert verify_model_file(entry)
    path.write_bytes(PAYLOAD + b"!")
    assert not verify_model_file(entry)


# ------------------------------------------------------------------ remove_model


def test_remove_asks_first_and_deletes(hub) -> None:
    entry = tiny_entry()
    download_model(entry, yes)
    with pytest.raises(InstallDeclined):
        remove_model(entry, lambda p: False)
    assert local_path(entry).exists()
    prompts: list[str] = []
    assert remove_model(entry, lambda p: prompts.append(p) or True) is True
    assert "Tiny Model" in prompts[0]
    assert not (models_dir() / "tiny-q4km").exists()
    assert remove_model(entry, yes) is False  # nothing left


# ------------------------------------------------------------------ best_ctx_for


def test_best_ctx_for_a_specific_model() -> None:
    catalog = load_catalog()
    nine = catalog.require("qwen3.5-9b-q4km")
    gpu = lambda v: HardwareInfo(vendor="nvidia", vram_gb=v, backend="cuda")  # noqa: E731
    assert best_ctx_for(gpu(16), nine) == 32768
    assert best_ctx_for(gpu(4), nine) is None  # does not fit at all
    assert best_ctx_for(HardwareInfo(vendor="cpu"), nine) is None


# ------------------------------------------------------------------ CLI


@pytest.fixture
def tiny_catalog(monkeypatch):
    catalog = Catalog(entries=[tiny_entry()])
    monkeypatch.setattr("qedcode.cli.load_catalog", lambda: catalog)
    return catalog


def test_cli_download_with_yes(tiny_catalog, hub) -> None:
    result = CliRunner().invoke(app, ["models", "download", "tiny-q4km", "--yes"])
    assert result.exit_code == 0, result.stdout
    assert "Ready:" in result.stdout and "qed models use tiny-q4km" in result.stdout
    assert verify_model_file(tiny_catalog.entries[0])


def test_cli_download_asks_and_respects_no(tiny_catalog, hub) -> None:
    result = CliRunner().invoke(app, ["models", "download", "tiny-q4km"], input="n\n")
    assert result.exit_code == 1
    assert "Download Tiny Model" in result.stdout
    assert hub.calls == []
    result = CliRunner().invoke(app, ["models", "download", "tiny-q4km"], input="y\n")
    assert result.exit_code == 0 and len(hub.calls) == 1


def test_cli_download_unknown_model_and_errors(tiny_catalog, hub, monkeypatch) -> None:
    result = CliRunner().invoke(app, ["models", "download", "nope", "--yes"])
    assert result.exit_code == 2
    assert "qed models list" in result.output
    monkeypatch.setattr(installer, "hf_hub_download", FakeHub(fail_first=True))
    result = CliRunner().invoke(app, ["models", "download", "tiny-q4km", "--yes"])
    assert result.exit_code == 2 and "internet connection" in result.output


def test_cli_use_sets_the_config_and_reports_missing_download(tiny_catalog) -> None:
    result = CliRunner().invoke(app, ["models", "use", "tiny-q4km"])
    assert result.exit_code == 0, result.output
    cfg = load_global()
    assert (cfg.local.backend, cfg.local.model) == ("llamacpp", "tiny-q4km")
    assert "qed models download tiny-q4km" in result.stdout


def test_cli_use_caps_the_context_to_what_fits(monkeypatch) -> None:
    cfg = load_global()
    cfg.hardware.vendor, cfg.hardware.vram_gb, cfg.hardware.backend = "nvidia", 4.0, "cuda"
    cfg.local.ctx = 32768
    save_global(cfg)
    result = CliRunner().invoke(app, ["models", "use", "qwen3.5-4b-q4km"])
    assert result.exit_code == 0, result.output
    assert load_global().local.ctx == 12288  # what fits on a 4 GB card
    result = CliRunner().invoke(app, ["models", "use", "qwen3.5-9b-q4km"])
    assert "may not fit" in result.stdout


def test_cli_use_accepts_a_gguf_path_and_rejects_unknown_ids(tmp_path: Path) -> None:
    gguf = tmp_path / "mine.gguf"
    gguf.write_bytes(b"x")
    result = CliRunner().invoke(app, ["models", "use", str(gguf)])
    assert result.exit_code == 0 and load_global().local.model == str(gguf)
    bad = CliRunner().invoke(app, ["models", "use", "not-a-model"])
    assert bad.exit_code == 2 and "qed models list" in bad.output


def test_cli_remove_asks_first(tiny_catalog, hub) -> None:
    entry = tiny_catalog.entries[0]
    download_model(entry, yes)
    declined = CliRunner().invoke(app, ["models", "remove", "tiny-q4km"], input="n\n")
    assert declined.exit_code == 1 and local_path(entry).exists()
    cfg = load_global()
    cfg.local.model = "tiny-q4km"
    save_global(cfg)
    done = CliRunner().invoke(app, ["models", "remove", "tiny-q4km", "--yes"])
    assert done.exit_code == 0 and not local_path(entry).exists()
    assert "model in use" in done.stdout
    again = CliRunner().invoke(app, ["models", "remove", "tiny-q4km", "--yes"])
    assert "nothing to remove" in again.stdout
