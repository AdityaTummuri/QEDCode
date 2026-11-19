from __future__ import annotations

import pytest
from typer.testing import CliRunner

from qedcode.cli import app
from qedcode.config import GlobalConfig, save_global
from qedcode.hardware.detect import HardwareInfo
from qedcode.hardware.recommend import (
    CTX_STEPS,
    kv_gb,
    need_gb,
    recommend,
)
from qedcode.runtime.catalog import CatalogError, load_catalog, parse_catalog


def gpu(vram: float, vendor: str = "nvidia") -> HardwareInfo:
    return HardwareInfo(vendor=vendor, gpu_name="Test GPU", vram_gb=vram, backend="cuda")


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


# ------------------------------------------------------------------ catalog


def test_packaged_catalog_loads_with_the_four_models(catalog) -> None:
    ids = {e.id for e in catalog.entries}
    assert ids == {
        "qwen3.5-9b-q4km",
        "qwen3.5-4b-q4km",
        "qwen2.5-coder-7b-q4km",
        "qwen3.5-2b-q4km",
    }
    for entry in catalog.entries:
        assert entry.verified, entry.unverified_fields()
        assert entry.gguf.size_bytes > 0
        assert abs(entry.gguf.size_gb - entry.gguf.size_bytes / 1e9) < 0.01
        assert len(entry.gguf.sha256) == 64
        assert entry.gguf.file.endswith(".gguf")
        assert entry.license == "Apache-2.0"


def test_kv_numbers_match_the_derivation_in_the_brief(catalog) -> None:
    assert catalog.require("qwen3.5-4b-q4km").kv_bytes_per_token_f16 == 8 * 2 * 4 * 256 * 2
    assert catalog.require("qwen2.5-coder-7b-q4km").kv_bytes_per_token_f16 == 28 * 2 * 4 * 128 * 2
    assert catalog.require("qwen3.5-2b-q4km").kv_bytes_per_token_f16 == 6 * 2 * 2 * 256 * 2


def test_unknown_id_error_is_helpful(catalog) -> None:
    with pytest.raises(CatalogError) as err:
        catalog.require("nope")
    assert "qed models list" in str(err.value)


def test_placeholders_are_flagged_and_skipped() -> None:
    text = """
- id: m1
  display: M1
  gguf: {repo: "<verify>", file: "<verify>.gguf", size_gb: "<verify>", sha256: "<fill>"}
  kv_bytes_per_token_f16: 1000
  rank: 1
"""
    cat = parse_catalog(text)
    entry = cat.entries[0]
    assert not entry.verified
    assert "gguf.size_gb" in entry.unverified_fields()
    assert entry.weights_gb is None
    assert need_gb(entry, 8192, "q8_0") is None
    rec = recommend(gpu(8), cat)
    assert rec.model_id == ""  # nothing verified, nothing recommended


@pytest.mark.parametrize(
    "text",
    [
        "not: a list",
        "- id: a\n  display: A\n  rank: 1\n",  # missing required fields
        "- id: a\n  display: A\n  gguf: {repo: r, file: f, size_gb: 1, sha256: XYZ}\n"
        "  kv_bytes_per_token_f16: 1\n  rank: 1\n",
        "- {id: a, display: A, gguf: {repo: r, file: f, size_gb: 1, sha256: '<fill>'}, "
        "kv_bytes_per_token_f16: 1, rank: 1}\n- {id: a, display: B, gguf: {repo: r, file: f, "
        "size_gb: 1, sha256: '<fill>'}, kv_bytes_per_token_f16: 1, rank: 2}\n",
        "[unclosed",
    ],
)
def test_bad_catalogs_give_plain_errors(text: str) -> None:
    with pytest.raises(CatalogError):
        parse_catalog(text)


# ------------------------------------------------------------------ formula


def test_formula_matches_the_brief(catalog) -> None:
    four = catalog.require("qwen3.5-4b-q4km")
    assert kv_gb(four, 8192, "f16") == pytest.approx(32768 * 8192 / 2**30)
    assert kv_gb(four, 8192, "q8_0") == pytest.approx(32768 * 8192 * 0.53 / 2**30)
    expected = 2.74 + kv_gb(four, 8192, "q8_0") + 50 / 1024 + 0.6 + 0.4
    assert need_gb(four, 8192, "q8_0", 0.4) == pytest.approx(expected)


# ------------------------------------------------------------------ recommendations


def test_4gb_card_gets_the_4b_model_with_a_smaller_context(catalog) -> None:
    rec = recommend(gpu(4.0), catalog)
    assert rec.model_id == "qwen3.5-4b-q4km"
    assert rec.run_on == "gpu"
    assert rec.ctx == 12288
    assert rec.need_gb <= 4.0


def test_6gb_card_gets_the_4b_model_with_a_big_context(catalog) -> None:
    rec = recommend(gpu(6.0), catalog)
    assert rec.model_id == "qwen3.5-4b-q4km"
    assert rec.ctx == 32768
    assert rec.need_gb <= 6.0
    assert recommend(gpu(4.0), catalog).ctx < rec.ctx


def test_8gb_card_gets_the_9b_model(catalog) -> None:
    rec = recommend(gpu(8.0), catalog)
    assert rec.model_id == "qwen3.5-9b-q4km"
    assert rec.need_gb <= 8.0
    assert rec.ctx in CTX_STEPS


def test_16gb_card_gets_the_9b_model_with_the_largest_context(catalog) -> None:
    rec = recommend(gpu(16.0), catalog)
    assert rec.model_id == "qwen3.5-9b-q4km"
    assert rec.ctx == 32768
    assert rec.kv_cache == "q8_0"


def test_apple_with_16gb_ram_has_9_6_gb_to_use_and_no_desktop_reserve(catalog) -> None:
    apple = HardwareInfo(vendor="apple", gpu_name="Apple M2", vram_gb=9.6, backend="metal")
    rec = recommend(apple, catalog)
    assert rec.model_id == "qwen3.5-9b-q4km"
    with_reserve = need_gb(catalog.require(rec.model_id), rec.ctx, "q8_0", 0.4)
    assert rec.need_gb < with_reserve  # Apple plans without the 0.4 GB desktop reserve


def test_cpu_only_gets_the_smallest_model_with_a_warning(catalog) -> None:
    rec = recommend(HardwareInfo(vendor="cpu", backend="cpu"), catalog)
    assert rec.model_id == "qwen3.5-2b-q4km"
    assert (rec.ctx, rec.run_on, rec.kv_cache) == (8192, "cpu", "f16")
    assert any("slow" in w for w in rec.warnings)


def test_a_tiny_gpu_falls_back_to_cpu_and_says_why(catalog) -> None:
    rec = recommend(gpu(1.5), catalog)
    assert rec.run_on == "cpu"
    assert rec.model_id == "qwen3.5-2b-q4km"
    assert "smallest model needs" in rec.reason


def test_a_smaller_gpu_never_gets_a_bigger_model(catalog) -> None:
    order = {e.id: e.weights_gb for e in catalog.entries}
    last = float("inf")
    for vram in (24, 16, 12, 8, 6, 4, 3, 2.5):
        rec = recommend(gpu(float(vram)), catalog)
        size = order[rec.model_id]
        assert size <= last + 1e-9
        last = size


# ------------------------------------------------------------------ CLI


def test_models_list_shows_the_catalog() -> None:
    result = CliRunner().invoke(app, ["models", "list"])
    assert result.exit_code == 0
    assert "qwen3.5-4b-q4km" in result.stdout
    assert "Qwen2.5-Coder" in result.stdout


def test_models_with_no_subcommand_lists() -> None:
    result = CliRunner().invoke(app, ["models"])
    assert result.exit_code == 0
    assert "qwen3.5-2b-q4km" in result.stdout


def test_models_recommend_reads_the_vram_from_the_config() -> None:
    def configured(vram: float) -> str:
        cfg = GlobalConfig()
        cfg.hardware.vendor, cfg.hardware.vram_gb, cfg.hardware.backend = "nvidia", vram, "cuda"
        save_global(cfg)
        result = CliRunner().invoke(app, ["models", "recommend"])
        assert result.exit_code == 0, result.stdout
        return result.stdout

    four = configured(4.0)
    six = configured(6.0)
    assert "12288" in four
    assert "32768" in six
    assert "Qwen3.5 4B" in six
