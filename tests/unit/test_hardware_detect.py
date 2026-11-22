from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from typer.testing import CliRunner

from qedcode.cli import app
from qedcode.config import HardwareConfig
from qedcode.hardware.detect import (
    HardwareInfo,
    Host,
    apple_usable_gb,
    classify_adapter,
    default_backend,
    detect,
    info_from_config,
    is_integrated_intel,
    parse_cuda_version,
    parse_nvidia_smi_csv,
    with_overrides,
)
from qedcode.verify.run import RunResult

GIB = 1024**3

NVIDIA_HEADER = """\
Thu Nov 20 10:00:00 2026
+-----------------------------------------------------------------------------------------+
| NVIDIA-SMI 560.94                 Driver Version: 560.94         CUDA Version: 12.6     |
+-----------------------------------------+------------------------+----------------------+
"""


def ok(stdout: str = "") -> RunResult:
    return RunResult(args=[], exit_code=0, stdout=stdout)


def missing(name: str = "x") -> RunResult:
    return RunResult(args=[], error=f"Could not start '{name}': not found")


def make_host(
    system: str = "Linux",
    machine: str = "x86_64",
    commands: dict[str, Callable[[list[str]], RunResult] | RunResult] | None = None,
    files: dict[str, str] | None = None,
    dirs: dict[str, list[str]] | None = None,
    adapters: list[tuple[str, int]] | Exception | None = None,
    ram: int | None = None,
) -> Host:
    commands = commands or {}
    files = files or {}
    dirs = dirs or {}

    def run_(args: list[str], timeout: float) -> RunResult:
        handler = commands.get(args[0])
        if handler is None:
            return missing(args[0])
        if isinstance(handler, RunResult):
            return handler
        return handler(args)

    def read_text(path: Path) -> str | None:
        return files.get(path.as_posix())

    def list_dir(path: Path) -> list[Path]:
        return [Path(p) for p in dirs.get(path.as_posix(), [])]

    def windows_adapters() -> list[tuple[str, int]]:
        if isinstance(adapters, Exception):
            raise adapters
        return adapters or []

    return Host(
        system=system,
        machine=machine,
        run=run_,
        read_text=read_text,
        list_dir=list_dir,
        windows_adapters=windows_adapters,
        total_ram_bytes=lambda: ram,
    )


def nvidia_smi(csv: str, header: str = NVIDIA_HEADER):
    def handler(args: list[str]) -> RunResult:
        return (
            ok(csv)
            if "--query-gpu=name,memory.total,driver_version" in " ".join(args)
            else ok(header)
        )

    return handler


# ------------------------------------------------------------------ pure helpers


def test_parse_nvidia_csv_and_cuda() -> None:
    rows = parse_nvidia_smi_csv("NVIDIA GeForce RTX 4050 Laptop GPU, 6141, 560.94\n\nbad line\n")
    assert rows == [("NVIDIA GeForce RTX 4050 Laptop GPU", 6.0, "560.94")]
    assert parse_cuda_version(NVIDIA_HEADER) == "12.6"
    assert parse_cuda_version("nothing here") == ""
    assert parse_nvidia_smi_csv("name, notanumber, 1") == []


def test_classify_and_helpers() -> None:
    assert classify_adapter("NVIDIA GeForce RTX 5060 Ti") == "nvidia"
    assert classify_adapter("AMD Radeon RX 7600") == "amd"
    assert classify_adapter("Intel(R) UHD Graphics 770") == "intel"
    assert classify_adapter("Microsoft Basic Display Adapter") is None
    assert is_integrated_intel("Intel(R) Iris(R) Xe Graphics")
    assert not is_integrated_intel("Intel(R) Arc(TM) A770 Graphics")
    assert apple_usable_gb(16 * GIB) == 9.6
    assert default_backend("nvidia", "Windows") == "cuda"
    assert default_backend("nvidia", "Linux") == "cuda"
    assert default_backend("amd", "Windows") == "vulkan"
    assert default_backend("apple", "Darwin") == "metal"
    assert default_backend("cpu", "Linux") == "cpu"


# ------------------------------------------------------------------ NVIDIA


def test_nvidia_on_windows() -> None:
    host = make_host(
        "Windows",
        "amd64",
        commands={"nvidia-smi": nvidia_smi("NVIDIA GeForce RTX 5060 Ti, 16311, 576.02\n")},
    )
    info = detect(host)
    assert info.vendor == "nvidia"
    assert info.gpu_name == "NVIDIA GeForce RTX 5060 Ti"
    assert info.vram_gb == 15.9
    assert info.backend == "cuda"
    assert info.driver == "576.02"
    assert info.cuda_version == "12.6"
    assert info.source == "nvidia-smi"


def test_nvidia_on_linux_uses_cuda() -> None:
    host = make_host(
        commands={"nvidia-smi": nvidia_smi("NVIDIA GeForce RTX 4050 Laptop GPU, 6141, 560.94\n")}
    )
    info = detect(host)
    assert (info.vendor, info.vram_gb, info.backend) == ("nvidia", 6.0, "cuda")


def test_two_nvidia_gpus_use_the_biggest() -> None:
    csv = "NVIDIA T400, 4096, 550.1\nNVIDIA RTX A4000, 16376, 550.1\n"
    info = detect(make_host("Windows", commands={"nvidia-smi": nvidia_smi(csv)}))
    assert info.gpu_name == "NVIDIA RTX A4000"
    assert any("T400" in n for n in info.notes)


def test_nvidia_smi_failure_adds_a_note_and_falls_through() -> None:
    failing = RunResult(args=[], exit_code=9, stderr="NVIDIA-SMI has failed")
    info = detect(make_host(commands={"nvidia-smi": failing}))
    assert info.vendor == "cpu"
    assert any("nvidia-smi failed" in n for n in info.notes)


# ------------------------------------------------------------------ AMD / Intel on Linux


def drm(card: str, vendor: str, device: str, vram: int | None = None, name: str | None = None):
    base = f"/sys/class/drm/{card}/device"
    files = {f"{base}/vendor": vendor + "\n", f"{base}/device": device + "\n"}
    if vram is not None:
        files[f"{base}/mem_info_vram_total"] = str(vram) + "\n"
    if name:
        files[f"{base}/product_name"] = name
    return files


def test_amd_on_linux_reads_sysfs_vram() -> None:
    files = drm("card1", "0x1002", "0x73bf", vram=8 * GIB)
    host = make_host(
        dirs={"/sys/class/drm": ["/sys/class/drm/card1", "/sys/class/drm/version"]}, files=files
    )
    info = detect(host)
    assert info.vendor == "amd"
    assert info.vram_gb == 8.0
    assert info.backend == "vulkan"
    assert info.source == "sysfs"
    assert "1002:73bf" in info.gpu_name


def test_amd_without_vram_file_notes_it() -> None:
    files = drm("card0", "0x1002", "0x1638", vram=None, name="AMD Radeon Graphics")
    host = make_host(dirs={"/sys/class/drm": ["/sys/class/drm/card0"]}, files=files)
    info = detect(host)
    assert info.vendor == "amd" and info.vram_gb == 0.0
    assert info.gpu_name == "AMD Radeon Graphics"
    assert any("by hand" in n for n in info.notes)


def test_intel_integrated_is_cpu_class() -> None:
    files = drm("card0", "0x8086", "0x9a49")
    host = make_host(dirs={"/sys/class/drm": ["/sys/class/drm/card0"]}, files=files)
    info = detect(host)
    assert info.vendor == "intel"
    assert info.vram_gb == 0.0
    assert info.backend == "cpu"
    assert any("Arc" in n for n in info.notes)


def test_intel_arc_with_vram_is_discrete() -> None:
    files = drm("card0", "0x8086", "0x56a0", vram=16 * GIB)
    host = make_host(dirs={"/sys/class/drm": ["/sys/class/drm/card0"]}, files=files)
    info = detect(host)
    assert (info.vendor, info.vram_gb, info.backend) == ("intel", 16.0, "vulkan")


def test_the_biggest_card_wins_on_linux() -> None:
    files = {**drm("card0", "0x8086", "0x9a49"), **drm("card1", "0x1002", "0x73bf", vram=12 * GIB)}
    host = make_host(
        dirs={"/sys/class/drm": ["/sys/class/drm/card0", "/sys/class/drm/card1"]}, files=files
    )
    assert detect(host).vendor == "amd"


def test_nvidia_card_in_sysfs_without_nvidia_smi() -> None:
    files = drm("card0", "0x10de", "0x2504")
    host = make_host(dirs={"/sys/class/drm": ["/sys/class/drm/card0"]}, files=files)
    info = detect(host)
    assert info.vendor == "nvidia" and info.vram_gb == 0.0
    assert any("nvidia-smi is not" in n for n in info.notes)


# ------------------------------------------------------------------ Windows AMD / Intel


def powershell(json_text: str):
    return {"powershell": ok(json_text)}


def test_amd_on_windows_uses_registry_not_adapterram() -> None:
    cim = '[{"Name":"AMD Radeon RX 7600","DriverVersion":"31.0.1"},{"Name":"Intel(R) UHD Graphics","DriverVersion":"30.0"}]'
    host = make_host(
        "Windows",
        "amd64",
        commands=powershell(cim),
        adapters=[("AMD Radeon RX 7600", 8 * GIB), ("Intel(R) UHD Graphics", 128 * 1024**2)],
    )
    info = detect(host)
    assert info.vendor == "amd"
    assert info.vram_gb == 8.0
    assert info.backend == "vulkan"
    assert info.driver == "31.0.1"
    assert info.source == "windows registry"


def test_windows_single_adapter_json_object_and_missing_registry() -> None:
    cim = '{"Name":"AMD Radeon RX 6600","DriverVersion":"1.0"}'
    info = detect(make_host("Windows", commands=powershell(cim), adapters=OSError("denied")))
    assert info.vendor == "amd" and info.vram_gb == 0.0
    assert any("registry" in n for n in info.notes)


def test_intel_integrated_on_windows_is_cpu_class_and_arc_is_not() -> None:
    cim = '{"Name":"Intel(R) Iris(R) Xe Graphics","DriverVersion":"1"}'
    info = detect(
        make_host(
            "Windows", commands=powershell(cim), adapters=[("Intel(R) Iris(R) Xe Graphics", GIB)]
        )
    )
    assert (info.vendor, info.vram_gb, info.backend) == ("intel", 0.0, "cpu")
    arc = '{"Name":"Intel(R) Arc(TM) A770 Graphics","DriverVersion":"1"}'
    info = detect(
        make_host(
            "Windows",
            commands=powershell(arc),
            adapters=[("Intel(R) Arc(TM) A770 Graphics", 16 * GIB)],
        )
    )
    assert (info.vendor, info.vram_gb, info.backend) == ("intel", 16.0, "vulkan")


def test_windows_powershell_failure_falls_back_to_cpu() -> None:
    info = detect(make_host("Windows", commands={"powershell": RunResult(args=[], exit_code=1)}))
    assert info.vendor == "cpu"
    assert any("PowerShell" in n for n in info.notes)


# ------------------------------------------------------------------ Apple, CPU


def test_apple_silicon_uses_sixty_percent_of_ram() -> None:
    def sysctl(args: list[str]) -> RunResult:
        if args[-1] == "hw.memsize":
            return ok(str(16 * GIB) + "\n")
        return ok("Apple M2 Pro\n")

    info = detect(make_host("Darwin", "arm64", commands={"sysctl": sysctl}))
    assert (info.vendor, info.backend, info.vram_gb) == ("apple", "metal", 9.6)
    assert info.gpu_name == "Apple M2 Pro"
    assert info.source == "sysctl"


def test_apple_sysctl_failure_uses_psutil_ram() -> None:
    info = detect(make_host("Darwin", "arm64", commands={"sysctl": missing("sysctl")}, ram=8 * GIB))
    assert info.vendor == "apple" and info.vram_gb == 4.8
    assert info.gpu_name == "Apple Silicon"


def test_apple_with_no_memory_info_falls_back_to_cpu() -> None:
    info = detect(make_host("Darwin", "arm64", commands={"sysctl": missing("sysctl")}, ram=None))
    assert info.vendor == "cpu"


def test_intel_mac_is_cpu_only() -> None:
    info = detect(make_host("Darwin", "x86_64"))
    assert info.vendor == "cpu"
    assert any("Intel Macs" in n for n in info.notes)


def test_cpu_only_linux_with_nothing_found() -> None:
    info = detect(make_host())
    assert (info.vendor, info.backend, info.vram_gb, info.source) == ("cpu", "cpu", 0.0, "fallback")
    assert any("slow" in n for n in info.notes)


def test_a_probe_that_raises_never_crashes_detection() -> None:
    def boom(args: list[str]) -> RunResult:
        raise RuntimeError("kaboom")

    info = detect(make_host(commands={"nvidia-smi": boom}))
    assert info.vendor == "cpu"
    assert any("kaboom" in n for n in info.notes)


# ------------------------------------------------------------------ overrides, config


def test_overrides_change_any_field_and_say_so() -> None:
    base = HardwareInfo(vendor="cpu", backend="cpu", source="fallback")
    out = with_overrides(base, vendor="nvidia", vram_gb=6.0, gpu_name="My GPU", system="Windows")
    assert (out.vendor, out.vram_gb, out.gpu_name, out.backend) == ("nvidia", 6.0, "My GPU", "cuda")
    assert out.source == "user override"
    assert any("Changed by you" in n for n in out.notes)
    assert base.vendor == "cpu"  # the original is untouched
    assert with_overrides(base, backend="vulkan").backend == "vulkan"
    assert with_overrides(base).source == "fallback"  # no change, no note


def test_config_round_trip() -> None:
    info = HardwareInfo(vendor="amd", gpu_name="g", vram_gb=8.0, backend="vulkan", driver="d")
    cfg = info.to_config()
    assert isinstance(cfg, HardwareConfig)
    again = info_from_config(cfg)
    assert (again.vendor, again.gpu_name, again.vram_gb, again.backend) == (
        "amd",
        "g",
        8.0,
        "vulkan",
    )
    assert again.source == "config"


# ------------------------------------------------------------------ qed doctor


def test_qed_doctor_shows_the_hardware_check(monkeypatch, tmp_path) -> None:
    info = HardwareInfo(
        vendor="nvidia",
        gpu_name="NVIDIA GeForce RTX 5060 Ti",
        vram_gb=15.9,
        backend="cuda",
        driver="576.02",
        cuda_version="12.8",
        source="nvidia-smi",
    )
    monkeypatch.setattr("qedcode.hardware.detect.current_hardware", lambda *a, **k: info)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["doctor"])
    assert "QEDCode doctor" in result.stdout and "hardware" in result.stdout
    assert "RTX 5060 Ti" in result.stdout and "15.9 GB" in result.stdout
    assert "nvidia-smi" in result.stdout


def test_a_default_cpu_entry_in_the_config_is_detected_again(monkeypatch) -> None:
    from qedcode.config import GlobalConfig, save_global
    from qedcode.hardware import detect as d

    save_global(GlobalConfig())  # e.g. written by `qed models use` before any `qed setup`
    found = d.HardwareInfo(vendor="nvidia", gpu_name="RTX 4050", vram_gb=6.0, backend="cuda")
    monkeypatch.setattr(d, "detect", lambda host=None: found)
    assert d.current_hardware() == found
    cfg = GlobalConfig()
    cfg.hardware.vendor = "amd"
    cfg.hardware.vram_gb = 8.0
    save_global(cfg)
    assert d.current_hardware().vendor == "amd"  # a real saved choice is kept
