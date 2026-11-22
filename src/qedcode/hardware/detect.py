"""Hardware detection (AGENTS.md section 10): GPU vendor, usable VRAM and a suggested backend.

`detect()` never raises. Every probe has a short timeout; when a probe fails it adds a note and
the next probe runs. The caller always shows the result and lets the user override every field.
"""

from __future__ import annotations

import json
import logging
import platform
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from qedcode.config import HardwareConfig, global_config_path, load_global
from qedcode.verify.run import RunResult, run

log = logging.getLogger("qedcode.hardware")

Vendor = Literal["nvidia", "amd", "intel", "apple", "cpu"]
Backend = Literal["cuda", "vulkan", "rocm", "hip", "metal", "cpu"]

PROBE_TIMEOUT_S = 5.0
APPLE_USABLE_FRACTION = 0.6  # unified memory: plan for 60% of RAM
PCI_AMD, PCI_INTEL, PCI_NVIDIA = "0x1002", "0x8086", "0x10de"
_WIN_DISPLAY_CLASS = (
    r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11cd-bfc1-08002be10318}"
)
_GIB = 1024**3


class HardwareInfo(BaseModel):
    """What was found about the computer's graphics hardware, and how."""

    vendor: Vendor = "cpu"
    gpu_name: str = ""
    vram_gb: float = 0.0
    backend: Backend = "cpu"
    driver: str = ""
    cuda_version: str = ""
    source: str = ""
    notes: list[str] = Field(default_factory=list)

    def to_config(self) -> HardwareConfig:
        """The part of this result that is saved in the global config."""
        return HardwareConfig(
            vendor=self.vendor, gpu_name=self.gpu_name, vram_gb=self.vram_gb, backend=self.backend
        )


@dataclass
class Host:
    """Everything detection reads from the machine, so tests can fake all of it."""

    system: str
    machine: str
    run: Callable[[list[str], float], RunResult]
    read_text: Callable[[Path], str | None]
    list_dir: Callable[[Path], list[Path]]
    windows_adapters: Callable[[], list[tuple[str, int]]]  # (DriverDesc, bytes of VRAM)
    total_ram_bytes: Callable[[], int | None]


# --------------------------------------------------------------------------- real host


def _real_read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def _real_list_dir(path: Path) -> list[Path]:
    try:
        return sorted(path.iterdir())
    except OSError:
        return []


def _real_windows_adapters() -> list[tuple[str, int]]:  # pragma: no cover - Windows only
    import winreg

    found: list[tuple[str, int]] = []
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _WIN_DISPLAY_CLASS) as key:
        index = 0
        while True:
            try:
                sub = winreg.EnumKey(key, index)
            except OSError:
                break
            index += 1
            if not sub.isdigit():
                continue
            try:
                with winreg.OpenKey(key, sub) as adapter:
                    desc = str(winreg.QueryValueEx(adapter, "DriverDesc")[0])
                    memory = winreg.QueryValueEx(adapter, "HardwareInformation.qwMemorySize")[0]
            except OSError:
                continue
            if isinstance(memory, bytes):
                memory = int.from_bytes(memory, "little")
            found.append((desc, int(memory)))
    return found


def _real_total_ram() -> int | None:
    try:
        import psutil

        return int(psutil.virtual_memory().total)
    except Exception:  # noqa: BLE001 - any failure just means "unknown"
        return None


def real_host() -> Host:
    """The Host for this computer."""
    return Host(
        system=platform.system(),
        machine=platform.machine().lower(),
        run=lambda args, timeout: run(args, timeout=timeout),
        read_text=_real_read_text,
        list_dir=_real_list_dir,
        windows_adapters=_real_windows_adapters,
        total_ram_bytes=_real_total_ram,
    )


# --------------------------------------------------------------------------- pure helpers


def default_backend(vendor: str, system: str) -> Backend:
    """The llama.cpp build family to suggest for a vendor on an operating system."""
    if vendor == "nvidia":
        return (
            "cuda"  # the installer falls back to Vulkan, then CPU, when a CUDA build will not run
        )
    if vendor in ("amd", "intel"):
        return "vulkan"
    if vendor == "apple":
        return "metal"
    return "cpu"


def parse_nvidia_smi_csv(text: str) -> list[tuple[str, float, str]]:
    """Parse `name,memory.total,driver_version` CSV lines into (name, vram_gb, driver)."""
    rows: list[tuple[str, float, str]] = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3 or not parts[0]:
            continue
        try:
            mib = float(parts[1])
        except ValueError:
            continue
        rows.append((parts[0], round(mib / 1024, 1), parts[2]))
    return rows


def parse_cuda_version(text: str) -> str:
    """Find 'CUDA Version: 12.6' in the plain nvidia-smi header; '' when absent."""
    match = re.search(r"CUDA Version:\s*([0-9]+(?:\.[0-9]+)?)", text)
    return match.group(1) if match else ""


def classify_adapter(name: str) -> Vendor | None:
    """Guess the vendor from an adapter name; None when it is not a GPU we know."""
    lowered = name.lower()
    if "nvidia" in lowered or "geforce" in lowered or "quadro" in lowered:
        return "nvidia"
    if "amd" in lowered or "radeon" in lowered:
        return "amd"
    if "intel" in lowered:
        return "intel"
    return None


def is_integrated_intel(name: str) -> bool:
    """Intel integrated graphics share system RAM; Arc cards are discrete."""
    return re.search(r"\barc\b", name.lower()) is None


def apple_usable_gb(ram_bytes: int) -> float:
    """Unified memory: plan for 60% of installed RAM."""
    return round(ram_bytes / _GIB * APPLE_USABLE_FRACTION, 1)


# --------------------------------------------------------------------------- probes


def _probe_nvidia(host: Host, notes: list[str]) -> HardwareInfo | None:
    query = host.run(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,driver_version",
            "--format=csv,noheader,nounits",
        ],
        PROBE_TIMEOUT_S,
    )
    if query.error:
        notes.append("nvidia-smi was not found, so no NVIDIA GPU was detected that way.")
        return None
    if not query.ok:
        notes.append(f"nvidia-smi failed (exit {query.exit_code}): {query.output[:120]}")
        return None
    rows = parse_nvidia_smi_csv(query.stdout)
    if not rows:
        notes.append("nvidia-smi ran but listed no GPU.")
        return None
    rows.sort(key=lambda r: r[1], reverse=True)
    name, vram_gb, driver = rows[0]
    info = HardwareInfo(
        vendor="nvidia",
        gpu_name=name,
        vram_gb=vram_gb,
        backend=default_backend("nvidia", host.system),
        driver=driver,
        source="nvidia-smi",
    )
    for other_name, other_vram, _ in rows[1:]:
        info.notes.append(f"Also found {other_name} ({other_vram} GB); using the largest GPU.")
    header = host.run(["nvidia-smi"], PROBE_TIMEOUT_S)
    if header.ok:
        info.cuda_version = parse_cuda_version(header.stdout)
    if not info.cuda_version:
        info.notes.append("Could not read the highest supported CUDA version from nvidia-smi.")
    return info


def _probe_apple(host: Host, notes: list[str]) -> HardwareInfo | None:
    if host.system != "Darwin" or host.machine != "arm64":
        return None
    mem = host.run(["sysctl", "-n", "hw.memsize"], PROBE_TIMEOUT_S)
    ram: int | None = None
    if mem.ok and mem.stdout.strip().isdigit():
        ram = int(mem.stdout.strip())
    else:
        notes.append("sysctl hw.memsize failed; asking the system for total RAM instead.")
        ram = host.total_ram_bytes()
    if not ram:
        notes.append("Could not read how much memory this Mac has.")
        return None
    brand = host.run(["sysctl", "-n", "machdep.cpu.brand_string"], PROBE_TIMEOUT_S)
    name = brand.stdout.strip() if brand.ok and brand.stdout.strip() else "Apple Silicon"
    return HardwareInfo(
        vendor="apple",
        gpu_name=name,
        vram_gb=apple_usable_gb(ram),
        backend="metal",
        source="sysctl",
        notes=[f"Unified memory: planning with {int(APPLE_USABLE_FRACTION * 100)}% of RAM."],
    )


def _sysfs_value(host: Host, path: Path) -> str | None:
    text = host.read_text(path)
    return text.strip().lower() if text else None


def _probe_linux_sysfs(host: Host, notes: list[str]) -> HardwareInfo | None:
    if host.system != "Linux":
        return None
    cards = [p for p in host.list_dir(Path("/sys/class/drm")) if re.fullmatch(r"card\d+", p.name)]
    if not cards:
        notes.append("No graphics cards were listed under /sys/class/drm.")
        return None
    best: HardwareInfo | None = None
    for card in cards:
        device = card / "device"
        vendor_id = _sysfs_value(host, device / "vendor")
        if vendor_id not in (PCI_AMD, PCI_INTEL, PCI_NVIDIA):
            continue
        candidate = _linux_card_info(host, device, vendor_id)
        if best is None or candidate.vram_gb > best.vram_gb:
            best = candidate
    if best is None:
        notes.append("No AMD, Intel or NVIDIA graphics card was found in /sys/class/drm.")
    return best


def _linux_card_info(host: Host, device: Path, vendor_id: str) -> HardwareInfo:
    device_id = _sysfs_value(host, device / "device") or "unknown"
    vram_bytes = _sysfs_value(host, device / "mem_info_vram_total")
    vram_gb = round(int(vram_bytes) / _GIB, 1) if vram_bytes and vram_bytes.isdigit() else 0.0
    product = host.read_text(device / "product_name")
    if vendor_id == PCI_AMD:
        info = HardwareInfo(
            vendor="amd",
            gpu_name=product or f"AMD GPU (PCI 1002:{device_id.removeprefix('0x')})",
            vram_gb=vram_gb,
            backend="vulkan",
            source="sysfs",
        )
        if vram_gb == 0.0:
            info.notes.append("Could not read AMD VRAM from sysfs; set it by hand in qed setup.")
        return info
    if vendor_id == PCI_NVIDIA:
        return HardwareInfo(
            vendor="nvidia",
            gpu_name=product or f"NVIDIA GPU (PCI 10de:{device_id.removeprefix('0x')})",
            vram_gb=0.0,
            backend="vulkan",
            source="sysfs",
            notes=[
                "An NVIDIA GPU is present but nvidia-smi is not; install the driver, or set the "
                "VRAM by hand in qed setup."
            ],
        )
    name = product or f"Intel GPU (PCI 8086:{device_id.removeprefix('0x')})"
    if vram_gb > 0:  # a discrete Arc card exposes its own memory
        return HardwareInfo(
            vendor="intel", gpu_name=name, vram_gb=vram_gb, backend="vulkan", source="sysfs"
        )
    return HardwareInfo(
        vendor="intel",
        gpu_name=name,
        vram_gb=0.0,
        backend="cpu",
        source="sysfs",
        notes=[
            "Integrated Intel graphics share system RAM, so this is treated as CPU-only. "
            "If it is a discrete Arc card, say so in qed setup."
        ],
    )


def _parse_video_controllers(text: str) -> list[tuple[str, str]]:
    """Parse Get-CimInstance JSON (one object or a list) into (name, driver version)."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    out: list[tuple[str, str]] = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict) and item.get("Name"):
            out.append((str(item["Name"]), str(item.get("DriverVersion") or "")))
    return out


def _probe_windows(host: Host, notes: list[str]) -> HardwareInfo | None:
    if host.system != "Windows":
        return None
    cim = host.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion"
            " | ConvertTo-Json -Compress",
        ],
        PROBE_TIMEOUT_S * 2,
    )
    controllers = _parse_video_controllers(cim.stdout) if cim.ok else []
    if not controllers:
        notes.append("PowerShell could not list the video controllers.")
        return None
    try:
        memory = {desc.lower(): size for desc, size in host.windows_adapters()}
    except Exception as exc:  # noqa: BLE001
        notes.append(f"Could not read GPU memory from the registry: {exc}")
        memory = {}
    candidates: list[HardwareInfo] = []
    for name, driver in controllers:
        vendor = classify_adapter(name)
        if vendor is None:
            continue
        size = memory.get(name.lower())
        vram = round(size / _GIB, 1) if size else 0.0
        info = HardwareInfo(
            vendor=vendor,
            gpu_name=name,
            vram_gb=vram,
            backend=default_backend(vendor, "Windows"),
            driver=driver,
            source="windows registry" if size else "powershell",
        )
        if vendor == "intel" and is_integrated_intel(name):
            info.vram_gb, info.backend = 0.0, "cpu"
            info.notes.append("Integrated Intel graphics share system RAM: treated as CPU-only.")
        elif not size:
            info.notes.append(
                "VRAM not found in the registry (AdapterRAM is not trusted: it caps at 4 GB); "
                "set it by hand in qed setup."
            )
        candidates.append(info)
    if not candidates:
        notes.append("No AMD, Intel or NVIDIA video controller was found.")
        return None
    candidates.sort(key=lambda c: c.vram_gb, reverse=True)
    return candidates[0]


def _cpu_fallback(host: Host, notes: list[str]) -> HardwareInfo:
    extra = "No supported GPU was found: QEDCode will run on the CPU, which is slow."
    if host.system == "Darwin" and host.machine != "arm64":
        extra += " Intel Macs use the CPU-only build."
    return HardwareInfo(vendor="cpu", backend="cpu", source="fallback", notes=[*notes, extra])


def detect(host: Host | None = None) -> HardwareInfo:
    """Find the GPU vendor, name and usable VRAM. Never raises; falls back to CPU-only."""
    host = host or real_host()
    notes: list[str] = []
    if host.system == "Darwin":
        probes = [_probe_apple]
    elif host.system == "Windows":
        probes = [_probe_nvidia, _probe_windows]
    else:
        probes = [_probe_nvidia, _probe_linux_sysfs]
    for probe in probes:
        try:
            info = probe(host, notes)
        except Exception as exc:  # noqa: BLE001 - detection must never crash the app
            log.warning("Hardware probe %s failed", probe.__name__, exc_info=True)
            notes.append(f"The {probe.__name__.removeprefix('_probe_')} probe failed: {exc}")
            continue
        if info is not None:
            info.notes = [*notes, *info.notes]
            return info
    return _cpu_fallback(host, notes)


# --------------------------------------------------------------------------- overrides


def info_from_config(cfg: HardwareConfig) -> HardwareInfo:
    """Turn a saved (possibly hand-edited) hardware config back into a HardwareInfo."""
    return HardwareInfo(
        vendor=cfg.vendor,
        gpu_name=cfg.gpu_name,
        vram_gb=cfg.vram_gb,
        backend=cfg.backend,
        source="config",
    )


def with_overrides(
    info: HardwareInfo,
    *,
    vendor: Vendor | None = None,
    gpu_name: str | None = None,
    vram_gb: float | None = None,
    backend: Backend | None = None,
    system: str | None = None,
) -> HardwareInfo:
    """Return a copy of `info` with the user's overrides applied (any field may be changed)."""
    data = info.model_copy(deep=True)
    changed: list[str] = []
    if vendor is not None and vendor != data.vendor:
        data.vendor = vendor
        changed.append("vendor")
        if backend is None:
            data.backend = default_backend(vendor, system or sys_platform_name())
    if gpu_name is not None:
        data.gpu_name = gpu_name
        changed.append("GPU name")
    if vram_gb is not None:
        data.vram_gb = vram_gb
        changed.append("VRAM")
    if backend is not None:
        data.backend = backend
        changed.append("backend")
    if changed:
        data.source = "user override"
        data.notes.append("Changed by you: " + ", ".join(changed) + ".")
    return data


def sys_platform_name() -> str:
    """'Windows', 'Linux' or 'Darwin' for the running system."""
    if sys.platform == "win32":
        return "Windows"
    return "Darwin" if sys.platform == "darwin" else "Linux"


def current_hardware(host: Host | None = None) -> HardwareInfo:
    """The hardware to plan with: the saved config when it names hardware, otherwise a fresh
    detection. A config saved before `qed setup` (for example by `qed models use`) holds only the
    default "cpu" entry, which says nothing about the machine, so it is detected again."""
    if global_config_path().exists():
        saved = load_global().hardware
        if saved.vendor != "cpu" or saved.gpu_name or saved.vram_gb:
            return info_from_config(saved)
    return detect(host)
