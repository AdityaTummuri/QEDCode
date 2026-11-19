"""Pick the best model and context size that fit the card (AGENTS.md section 10).

need_gb = weights_gb + kv_gb + state_gb + 0.6 (compute buffers) + reserve_gb
kv_gb   = kv_bytes_per_token_f16 x ctx x (0.53 if KV is q8_0 else 1.0) / 2^30
reserve = 0.4 GB when a desktop runs on the same GPU, else 0
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from qedcode.hardware.detect import HardwareInfo
from qedcode.runtime.catalog import Catalog, CatalogEntry

COMPUTE_BUFFER_GB = 0.6
DESKTOP_RESERVE_GB = 0.4
KV_Q8_FACTOR = 0.53
BASE_CTX = 8192
CTX_STEPS = [8192, 12288, 16384, 24576, 32768]
_GIB = 1024**3


class Recommendation(BaseModel):
    model_id: str
    display: str = ""
    ctx: int = BASE_CTX
    kv_cache: Literal["f16", "q8_0"] = "q8_0"
    need_gb: float = 0.0
    usable_gb: float = 0.0
    run_on: Literal["gpu", "cpu"] = "gpu"
    reason: str = ""
    warnings: list[str] = Field(default_factory=list)


def kv_gb(entry: CatalogEntry, ctx: int, kv_cache: str) -> float:
    """Size of the KV cache in GiB for a context length."""
    factor = KV_Q8_FACTOR if kv_cache == "q8_0" else 1.0
    return entry.kv_bytes_per_token_f16 * ctx * factor / _GIB


def need_gb(entry: CatalogEntry, ctx: int, kv_cache: str, reserve_gb: float = 0.0) -> float | None:
    """VRAM a model needs at a context length; None when its size is still unverified."""
    weights = entry.weights_gb
    if weights is None:
        return None
    state_gb = entry.state_mb_per_seq / 1024
    return weights + kv_gb(entry, ctx, kv_cache) + state_gb + COMPUTE_BUFFER_GB + reserve_gb


def reserve_for(hw: HardwareInfo, desktop_on_gpu: bool = True) -> float:
    """Discrete GPUs on a desktop OS also draw the screen; Apple's unified memory is planned."""
    return DESKTOP_RESERVE_GB if desktop_on_gpu and hw.vendor in ("nvidia", "amd", "intel") else 0.0


def _usable(entries: list[CatalogEntry]) -> list[CatalogEntry]:
    return [e for e in entries if e.weights_gb is not None]


def _cpu_recommendation(catalog: Catalog, hw: HardwareInfo, why: str) -> Recommendation:
    candidates = _usable(catalog.entries)
    if not candidates:
        return Recommendation(
            model_id="",
            run_on="cpu",
            reason="The catalog has no verified models yet.",
            warnings=["No model in the catalog has a verified size."],
        )
    smallest = min(candidates, key=lambda e: e.weights_gb or 0.0)
    return Recommendation(
        model_id=smallest.id,
        display=smallest.display,
        ctx=BASE_CTX,
        kv_cache="f16",
        need_gb=round(need_gb(smallest, BASE_CTX, "f16") or 0.0, 2),
        usable_gb=hw.vram_gb,
        run_on="cpu",
        reason=why,
        warnings=["Running on the CPU is slow. Use the Fast speed, or a smaller task."],
    )


def recommend(hw: HardwareInfo, catalog: Catalog, *, desktop_on_gpu: bool = True) -> Recommendation:
    """Return the best-ranked model that fits at 8192 tokens, with the largest context that fits."""
    if hw.vendor == "cpu" or hw.vram_gb <= 0:
        return _cpu_recommendation(
            catalog,
            hw,
            "No usable GPU memory was found, so the smallest model will run on the CPU.",
        )
    reserve = reserve_for(hw, desktop_on_gpu)
    usable = hw.vram_gb
    for entry in sorted(_usable(catalog.entries), key=lambda e: e.rank):
        base = need_gb(entry, BASE_CTX, "q8_0", reserve)
        if base is None or base > usable:
            continue
        ctx, needed = BASE_CTX, base
        for step in CTX_STEPS:
            candidate = need_gb(entry, step, "q8_0", reserve)
            if candidate is not None and candidate <= usable:
                ctx, needed = step, candidate
        return Recommendation(
            model_id=entry.id,
            display=entry.display,
            ctx=ctx,
            kv_cache="q8_0",
            need_gb=round(needed, 2),
            usable_gb=usable,
            run_on="gpu",
            reason=(
                f"{entry.display} with a {ctx}-token context needs about {needed:.1f} GB and your "
                f"GPU has {usable:.1f} GB to use."
            ),
        )
    smallest = min(_usable(catalog.entries), key=lambda e: e.weights_gb or 0.0, default=None)
    need = need_gb(smallest, BASE_CTX, "q8_0", reserve) if smallest else None
    why = (
        f"Even the smallest model needs about {need:.1f} GB and your GPU has {usable:.1f} GB, "
        "so it will run on the CPU."
        if need is not None
        else "No model in the catalog has a verified size."
    )
    return _cpu_recommendation(catalog, hw, why)
