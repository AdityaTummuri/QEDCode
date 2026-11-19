"""GPU memory use for the TUI header (AGENTS.md section 10): NVIDIA via nvidia-ml-py (optional
extra), AMD on Linux via sysfs, otherwise "n/a". Never raises."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

_GIB = 1024**3


def _nvidia() -> tuple[float, float] | None:
    try:
        import pynvml  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        pynvml.nvmlInit()
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            return info.used / _GIB, info.total / _GIB
        finally:
            pynvml.nvmlShutdown()
    except Exception:  # noqa: BLE001 - no driver, no GPU, an old library: all mean "unknown"
        return None


def _amd_sysfs(root: Path = Path("/sys/class/drm")) -> tuple[float, float] | None:
    if not sys.platform.startswith("linux") and root == Path("/sys/class/drm"):
        return None
    try:
        for card in sorted(root.glob("card[0-9]*")):
            device = card / "device"
            if (device / "vendor").read_text().strip().lower() != "0x1002":
                continue
            used = int((device / "mem_info_vram_used").read_text().strip())
            total = int((device / "mem_info_vram_total").read_text().strip())
            return used / _GIB, total / _GIB
    except (OSError, ValueError):
        return None
    return None


def vram_usage(
    vendor: str = "",
    *,
    nvidia: Callable[[], tuple[float, float] | None] = _nvidia,
    amd: Callable[[], tuple[float, float] | None] = _amd_sysfs,
) -> tuple[float, float] | None:
    """(used GB, total GB) of the GPU memory for this vendor, or None when it cannot be read."""
    if vendor == "nvidia":
        return nvidia()
    if vendor == "amd":
        return amd()
    return None


def format_vram(usage: tuple[float, float] | None) -> str:
    """`3.1/6.0 GB` or `n/a`."""
    if usage is None:
        return "n/a"
    used, total = usage
    return f"{used:.1f}/{total:.1f} GB"
