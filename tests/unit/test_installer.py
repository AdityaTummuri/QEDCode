from __future__ import annotations

import hashlib
import io
import json
import sys
import tarfile
import zipfile
from pathlib import Path

import httpx
import pytest

from qedcode.config import bin_dir
from qedcode.hardware.detect import HardwareInfo
from qedcode.runtime import installer
from qedcode.runtime.installer import (
    Asset,
    EnginePlan,
    InstallDeclined,
    InstallError,
    download_llamacpp,
    engine_dir,
    extract_archive,
    find_installed_engine,
    find_llamacpp_asset,
    find_llamacpp_assets,
    format_size,
    min_cuda_for,
    parse_build_asset,
)
from qedcode.verify.run import RunResult
from tests.conftest import REPO_ROOT

RELEASE = json.loads(
    (REPO_ROOT / "tests" / "fixtures" / "llamacpp_release_b12549.json").read_text(encoding="utf-8")
)


def hw(vendor: str, backend: str, gpu: str = "Test GPU", cuda: str = "") -> HardwareInfo:
    return HardwareInfo(vendor=vendor, backend=backend, gpu_name=gpu, cuda_version=cuda)


def names(plan: EnginePlan) -> list[str]:
    return [a.name for a in plan.assets]


def choose(info: HardwareInfo, system: str, machine: str = "x86_64") -> list[EnginePlan]:
    return find_llamacpp_assets(info, RELEASE, system=system, machine=machine)


# ------------------------------------------------------------------ the table, row by row


def test_windows_nvidia_cuda_then_vulkan_then_cpu() -> None:
    plans = choose(hw("nvidia", "cuda", "NVIDIA GeForce RTX 4050 Laptop GPU", "12.6"), "Windows")
    assert [p.backend for p in plans] == ["cuda", "vulkan", "cpu"]
    assert names(plans[0]) == [
        "llama-b12549-bin-win-cuda-12.4-x64.zip",
        "cudart-llama-bin-win-cuda-12.4-x64.zip",
    ]
    assert names(plans[1]) == ["llama-b12549-bin-win-vulkan-x64.zip"]
    assert names(plans[2]) == ["llama-b12549-bin-win-cpu-x64.zip"]
    assert plans[0].tag == "b12549"
    assert "12.6" in plans[0].reason


def test_rtx_50_series_needs_cuda_12_8_or_newer() -> None:
    gpu = "NVIDIA GeForce RTX 5060 Ti"
    # a driver that only supports CUDA 12.8: the 12.4 build is too old, the 13.5 build too new
    plans = choose(hw("nvidia", "cuda", gpu, "12.8"), "Windows")
    assert [p.backend for p in plans] == ["vulkan", "cpu"]
    # a driver that supports CUDA 13.5 gets the 13.5 build
    plans = choose(hw("nvidia", "cuda", gpu, "13.5"), "Windows")
    assert plans[0].backend == "cuda"
    assert "llama-b12549-bin-win-cuda-13.5-x64.zip" in names(plans[0])
    assert "cudart-llama-bin-win-cuda-13.5-x64.zip" in names(plans[0])


def test_unknown_driver_cuda_picks_the_most_compatible_build() -> None:
    plans = choose(hw("nvidia", "cuda", "NVIDIA GeForce RTX 3060", ""), "Windows")
    assert "llama-b12549-bin-win-cuda-12.4-x64.zip" in names(plans[0])
    blackwell = choose(hw("nvidia", "cuda", "NVIDIA GeForce RTX 5090", ""), "Windows")
    assert "llama-b12549-bin-win-cuda-13.5-x64.zip" in names(blackwell[0])


def test_linux_nvidia_uses_the_ubuntu_cuda_build_with_its_runtime() -> None:
    plans = choose(hw("nvidia", "cuda", "NVIDIA GeForce RTX 4050", "12.8"), "Linux")
    assert names(plans[0]) == [
        "llama-b12549-bin-ubuntu-cuda-12.8-x64.tar.gz",
        "cudart-llama-b12549-bin-ubuntu-cuda-12.8-x64.tar.gz",
    ]
    assert [p.backend for p in plans] == ["cuda", "vulkan", "cpu"]
    newer = choose(hw("nvidia", "cuda", "NVIDIA GeForce RTX 4050", "13.5"), "Linux")
    assert "llama-b12549-bin-ubuntu-cuda-13.5-x64.tar.gz" in names(newer[0])


def test_amd_defaults_to_vulkan_and_can_choose_rocm() -> None:
    linux = choose(hw("amd", "vulkan", "AMD Radeon RX 7600"), "Linux")
    assert names(linux[0]) == ["llama-b12549-bin-ubuntu-vulkan-x64.tar.gz"]
    assert names(linux[1]) == ["llama-b12549-bin-ubuntu-x64.tar.gz"]
    rocm = choose(hw("amd", "rocm", "AMD Radeon RX 7900"), "Linux")
    assert rocm[0].backend == "rocm"
    assert names(rocm[0]) == ["llama-b12549-bin-ubuntu-rocm-10.0-x64.tar.gz"]
    assert [p.backend for p in rocm] == ["rocm", "vulkan", "cpu"]
    win = choose(hw("amd", "vulkan", "AMD Radeon RX 7600"), "Windows")
    assert names(win[0]) == ["llama-b12549-bin-win-vulkan-x64.zip"]
    win_hip = choose(hw("amd", "hip", "AMD Radeon RX 7900"), "Windows")
    assert names(win_hip[0]) == ["llama-b12549-bin-win-rocm-10.0-x64.zip"]


def test_intel_vulkan_for_discrete_and_cpu_for_integrated() -> None:
    arc = choose(hw("intel", "vulkan", "Intel Arc A770"), "Windows")
    assert names(arc[0]) == ["llama-b12549-bin-win-vulkan-x64.zip"]
    linux_arc = choose(hw("intel", "vulkan", "Intel Arc A770"), "Linux")
    assert names(linux_arc[0]) == ["llama-b12549-bin-ubuntu-vulkan-x64.tar.gz"]
    integrated = choose(hw("intel", "cpu", "Intel UHD"), "Linux")
    assert [names(p) for p in integrated] == [["llama-b12549-bin-ubuntu-x64.tar.gz"]]


def test_apple_silicon_and_intel_mac() -> None:
    arm = choose(hw("apple", "metal", "Apple M2"), "Darwin", "arm64")
    assert names(arm[0]) == ["llama-b12549-bin-macos-arm64.tar.gz"]
    assert arm[0].backend == "metal"
    intel = choose(hw("cpu", "cpu"), "Darwin", "x86_64")
    assert names(intel[0]) == ["llama-b12549-bin-macos-x64.tar.gz"]


def test_cpu_only_on_each_system_and_arm64_linux() -> None:
    cpu = hw("cpu", "cpu")
    assert names(choose(cpu, "Windows")[0]) == ["llama-b12549-bin-win-cpu-x64.zip"]
    assert names(choose(cpu, "Linux")[0]) == ["llama-b12549-bin-ubuntu-x64.tar.gz"]
    assert names(choose(cpu, "Linux", "aarch64")[0]) == ["llama-b12549-bin-ubuntu-arm64.tar.gz"]
    assert names(choose(cpu, "Windows", "ARM64")[0]) == ["llama-b12549-bin-win-cpu-arm64.zip"]


def test_find_llamacpp_asset_returns_the_best_or_none() -> None:
    best = find_llamacpp_asset(hw("amd", "vulkan"), RELEASE, system="Linux", machine="x86_64")
    assert best is not None and best.backend == "vulkan"
    assert find_llamacpp_asset(hw("cpu", "cpu"), {"tag_name": "b1", "assets": []}) is None
    only_text = {
        "tag_name": "v1.0.0",
        "assets": [{"name": "nightly-tag.txt", "size": 1, "browser_download_url": "x"}],
    }
    assert find_llamacpp_assets(hw("nvidia", "cuda"), only_text, system="Linux") == []


def test_unwanted_assets_are_ignored() -> None:
    for name in (
        "llama-b12549-bin-android-arm64.tar.gz",
        "llama-b12549-bin-ubuntu-openvino-2026.4.1-x64.tar.gz",
        "llama-b12549-bin-ubuntu-sycl-fp16-x64.tar.gz",
        "llama-b12549-bin-ubuntu-s390x.tar.gz",
        "llama-b12549-xcframework.zip",
        "cudart-llama-bin-win-cuda-12.4-x64.zip",
    ):
        assert parse_build_asset({"name": name, "browser_download_url": "u", "size": 1}) is None
    for plan in choose(hw("nvidia", "cuda", "RTX 3060", "13.5"), "Linux"):
        assert all("openvino" not in n and "sycl" not in n for n in names(plan))


def test_min_cuda_for_blackwell_only() -> None:
    assert min_cuda_for("NVIDIA GeForce RTX 5060 Ti") == (12, 8)
    assert min_cuda_for("NVIDIA GeForce RTX 5090") == (12, 8)
    assert min_cuda_for("NVIDIA B200") == (12, 8)
    assert min_cuda_for("NVIDIA GeForce RTX 4090") == ()
    assert min_cuda_for("NVIDIA GeForce RTX 3050") == ()


def test_digests_sizes_and_urls_come_from_the_release() -> None:
    plan = choose(hw("amd", "vulkan"), "Windows")[0]
    asset = plan.assets[0]
    assert asset.url.startswith("https://github.com/ggml-org/llama.cpp/releases/download/b12549/")
    assert len(asset.sha256) == 64
    assert asset.size_bytes > 1_000_000
    assert plan.total_bytes == asset.size_bytes
    assert format_size(asset.size_bytes).endswith("MB")
    assert format_size(3 * 1024**3) == "3.0 GB"


# ------------------------------------------------------------------ downloading


def make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def make_targz(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


SERVER_NAME = "llama-server.exe" if sys.platform == "win32" else "llama-server"


def plan_for(
    backend: str, payload: bytes, *, name: str = "llama-b1-bin-x.zip", sha: str | None = None
):
    asset = Asset(
        name=name,
        url=f"https://github.com/ggml-org/llama.cpp/releases/download/b1/{name}",
        size_bytes=len(payload),
        sha256=sha if sha is not None else hashlib.sha256(payload).hexdigest(),
    )
    return EnginePlan("b1", backend, [asset], reason="test")


def http_for(payloads: dict[str, bytes]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        if name not in payloads:
            return httpx.Response(404)
        return httpx.Response(200, content=payloads[name])

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def fake_version(monkeypatch):
    """Pretend `llama-server --version` works unless the build folder name says otherwise."""
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append([str(a) for a in args])
        broken = "badcuda" in str(args[0])
        return RunResult(
            args=[str(a) for a in args],
            exit_code=1 if broken else 0,
            stderr="" if broken else "version: 1 (test)",
        )

    monkeypatch.setattr(installer, "run", fake_run)
    return calls


def test_declining_downloads_nothing(fake_version) -> None:
    payload = make_zip({f"llama/{SERVER_NAME}": b"bin"})
    asked: list[str] = []

    def no(prompt: str) -> bool:
        asked.append(prompt)
        return False

    with pytest.raises(InstallDeclined) as err:
        download_llamacpp(
            plan_for("vulkan", payload), no, http=http_for({"llama-b1-bin-x.zip": payload})
        )
    assert "you said no" in str(err.value)
    assert "vulkan build of llama.cpp b1" in asked[0]
    assert "MB" in asked[0] or "GB" in asked[0]
    assert not engine_dir("b1", "vulkan").exists()
    assert not fake_version


def test_install_extracts_runs_version_and_writes_the_manifest(fake_version) -> None:
    payload = make_zip({f"llama-b1/{SERVER_NAME}": b"bin", "llama-b1/ggml.dll": b"lib"})
    progress: list[tuple[str, int, int]] = []
    engine = download_llamacpp(
        plan_for("vulkan", payload),
        lambda p: True,
        lambda what, done, total: progress.append((what, done, total)),
        http=http_for({"llama-b1-bin-x.zip": payload}),
    )
    assert engine.backend == "vulkan" and engine.tag == "b1"
    assert engine.binary.name == SERVER_NAME
    assert engine.binary.is_relative_to(bin_dir())
    assert engine.directory == engine_dir("b1", "vulkan")
    assert engine.version == "version: 1 (test)"
    assert (engine.bin_dir / "ggml.dll").exists()
    assert fake_version[0][1] == "--version"
    assert progress and progress[-1][1] == len(payload)
    manifest = json.loads((engine.directory / "engine.json").read_text(encoding="utf-8"))
    assert manifest["binary"].endswith(SERVER_NAME)
    assert not (bin_dir() / ".downloads" / "llama-b1-bin-x.zip").exists()  # archive cleaned up
    found = find_installed_engine(hw("amd", "vulkan"), "b1", "Linux")
    assert found is not None and found.binary == engine.binary
    assert find_installed_engine(hw("amd", "vulkan"), "other-tag", "Linux") is None


def test_tar_gz_archives_work_too(fake_version) -> None:
    payload = make_targz({f"llama-b1/{SERVER_NAME}": b"bin"})
    plan = plan_for("cpu", payload, name="llama-b1-bin-x.tar.gz")
    engine = download_llamacpp(
        plan, lambda p: True, http=http_for({"llama-b1-bin-x.tar.gz": payload})
    )
    assert engine.binary.exists()


def test_a_build_that_will_not_start_falls_back_to_the_next(fake_version, monkeypatch) -> None:
    good = make_zip({f"llama/{SERVER_NAME}": b"bin"})
    bad = make_zip({f"llama/{SERVER_NAME}": b"bin"})
    monkeypatch.setattr(
        installer,
        "engine_dir",
        lambda tag, backend: (
            bin_dir() / "llama.cpp" / tag / ("badcuda" if backend == "cuda" else backend)
        ),
    )
    plans = [plan_for("cuda", bad, name="cuda.zip"), plan_for("vulkan", good, name="vulkan.zip")]
    prompts: list[str] = []
    engine = download_llamacpp(
        plans,
        lambda p: prompts.append(p) or True,
        http=http_for({"cuda.zip": bad, "vulkan.zip": good}),
    )
    assert engine.backend == "vulkan"
    assert len(prompts) == 2  # asked again, with the next build's size
    assert not (bin_dir() / "llama.cpp" / "b1" / "badcuda").exists()  # the broken one is removed


def test_checksum_mismatch_is_rejected(fake_version) -> None:
    payload = make_zip({f"llama/{SERVER_NAME}": b"bin"})
    plan = plan_for("cpu", payload, sha="0" * 64)
    with pytest.raises(InstallError) as err:
        download_llamacpp(plan, lambda p: True, http=http_for({"llama-b1-bin-x.zip": payload}))
    assert "checksum" in str(err.value)
    assert not engine_dir("b1", "cpu").exists()


def test_all_builds_failing_gives_one_clear_error(fake_version) -> None:
    payload = make_zip({"readme.txt": b"no server in here"})
    with pytest.raises(InstallError) as err:
        download_llamacpp(
            plan_for("cpu", payload), lambda p: True, http=http_for({"llama-b1-bin-x.zip": payload})
        )
    assert "did not contain llama-server" in str(err.value)
    assert "Ollama" in str(err.value)


def test_non_official_urls_are_refused(fake_version) -> None:
    payload = make_zip({f"llama/{SERVER_NAME}": b"bin"})
    asset = Asset("evil.zip", "https://example.com/fork/evil.zip", len(payload), "")
    with pytest.raises(InstallError) as err:
        download_llamacpp(
            EnginePlan("b1", "cpu", [asset]), lambda p: True, http=http_for({"evil.zip": payload})
        )
    assert "official" in str(err.value)


def test_download_errors_are_plain_english(fake_version) -> None:
    plan = plan_for("cpu", b"x")
    with pytest.raises(InstallError) as err:
        download_llamacpp(plan, lambda p: True, http=http_for({}))
    assert "HTTP 404" in str(err.value)

    def boom(request):
        raise httpx.ConnectError("offline", request=request)

    with pytest.raises(InstallError) as err2:
        download_llamacpp(
            plan, lambda p: True, http=httpx.Client(transport=httpx.MockTransport(boom))
        )
    assert "internet connection" in str(err2.value)


def test_no_plans_at_all_is_an_error() -> None:
    with pytest.raises(InstallError) as err:
        download_llamacpp([], lambda p: True)
    assert "No llama.cpp build matches" in str(err.value)


# ------------------------------------------------------------------ extraction safety


def test_zip_path_traversal_is_refused(tmp_path: Path) -> None:
    archive = tmp_path / "evil.zip"
    archive.write_bytes(make_zip({"../escape.txt": b"x"}))
    with pytest.raises(InstallError):
        extract_archive(archive, tmp_path / "out")
    assert not (tmp_path / "escape.txt").exists()


def test_tar_path_traversal_is_refused(tmp_path: Path) -> None:
    archive = tmp_path / "evil.tar.gz"
    archive.write_bytes(make_targz({"../escape.txt": b"x"}))
    with pytest.raises((InstallError, tarfile.TarError)):
        extract_archive(archive, tmp_path / "out")
    assert not (tmp_path / "escape.txt").exists()


# ------------------------------------------------------------------ release lookup


def test_fetch_release_errors_are_human() -> None:
    def respond(status: int):
        return httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(status, json={}))
        )

    with pytest.raises(InstallError) as err:
        installer.fetch_release("b0", http=respond(404))
    assert "does not exist" in str(err.value) and "llamacpp_tag" in str(err.value)
    with pytest.raises(InstallError) as err2:
        installer.fetch_release("b0", http=respond(403))
    assert "limiting" in str(err2.value)
    ok = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=RELEASE)))
    assert installer.fetch_release("b12549", http=ok)["tag_name"] == "b12549"


def test_newest_release_tag_skips_releases_without_builds() -> None:
    releases = [{"tag_name": "v1.0.0", "assets": [{"name": "nightly-tag.txt"}]}, RELEASE]
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=releases))
    )
    assert installer.newest_release_tag(http=client) == "b12549"
