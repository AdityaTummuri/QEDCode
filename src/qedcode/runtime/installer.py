"""Download and install llama.cpp builds (and, in step 2.5, models). Always asks first.

Only official ggml-org/llama.cpp release assets are downloaded, never forks. Everything lands
under QEDCODE_HOME (default ~/.qedcode). Nothing here is ever called from tests with a real network.
"""

from __future__ import annotations

import hashlib
import json
import logging
import platform
import re
import shutil
import sys
import tarfile
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from qedcode.config import bin_dir, models_dir
from qedcode.extras import MissingExtra, load_extra
from qedcode.hardware.detect import HardwareInfo, sys_platform_name
from qedcode.runtime.catalog import CatalogEntry, local_path
from qedcode.verify.run import run

log = logging.getLogger("qedcode.installer")

GITHUB_API = "https://api.github.com/repos/ggml-org/llama.cpp"
OFFICIAL_DOWNLOAD_PREFIX = "https://github.com/ggml-org/llama.cpp/releases/download/"
DEFAULT_LLAMACPP_TAG = "b12549"  # the release the asset matcher was written and checked against
BLACKWELL_MIN_CUDA = (12, 8)
ENGINE_MANIFEST = "engine.json"
ProgressFn = Callable[[str, int, int], None]  # (what, bytes done, bytes total)
ConfirmFn = Callable[[str], bool]


class InstallError(RuntimeError):
    """An install failed. The message says what happened, why, and what to do next."""


class InstallDeclined(InstallError):
    """The user said no when asked to download something."""


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    size_bytes: int
    sha256: str = ""


@dataclass
class EnginePlan:
    """One llama.cpp build to install: its assets and why it was chosen."""

    tag: str
    backend: str  # cuda | vulkan | cpu | metal | rocm
    assets: list[Asset]
    reason: str = ""

    @property
    def total_bytes(self) -> int:
        return sum(a.size_bytes for a in self.assets)


@dataclass
class InstalledEngine:
    tag: str
    backend: str
    directory: Path
    binary: Path
    version: str = ""

    @property
    def bin_dir(self) -> Path:
        return self.binary.parent


# --------------------------------------------------------------------------- asset matching

_BUILD = re.compile(
    r"^llama-(?P<tag>b\d+)-bin-(?P<os>win|ubuntu|macos)-(?:(?P<mid>.+?)-)?"
    r"(?P<arch>x64|arm64)\.(?:zip|tar\.gz)$"
)
_CUDART = re.compile(
    r"^cudart-llama-(?:(?P<tag>b\d+)-)?bin-(?P<os>win|ubuntu)-cuda-(?P<ver>[\d.]+)-"
    r"(?P<arch>x64|arm64)\.(?:zip|tar\.gz)$"
)
_OS_KEY = {"Windows": "win", "Linux": "ubuntu", "Darwin": "macos"}


@dataclass(frozen=True)
class ParsedAsset:
    os_key: str
    arch: str
    backend: str
    version: tuple[int, ...]
    asset: Asset


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", text))


def _asset_from_json(item: dict) -> Asset:
    digest = str(item.get("digest") or "")
    return Asset(
        name=str(item["name"]),
        url=str(item["browser_download_url"]),
        size_bytes=int(item.get("size") or 0),
        sha256=digest.removeprefix("sha256:") if digest.startswith("sha256:") else "",
    )


def parse_build_asset(item: dict) -> ParsedAsset | None:
    """Parse one release asset into (os, arch, backend, version); None for builds we do not use."""
    name = str(item.get("name", ""))
    match = _BUILD.match(name)
    if not match:
        return None
    mid = match.group("mid") or ""
    os_key = match.group("os")
    if not mid:
        backend, ver = ("metal" if os_key == "macos" else "cpu"), ""
    else:
        backend, _, ver = mid.partition("-")
    if backend not in {"cuda", "vulkan", "cpu", "rocm", "metal"}:
        return None
    return ParsedAsset(os_key, match.group("arch"), backend, _version(ver), _asset_from_json(item))


def parse_cudart_asset(item: dict) -> tuple[str, str, tuple[int, ...], Asset] | None:
    match = _CUDART.match(str(item.get("name", "")))
    if not match:
        return None
    return (
        match.group("os"),
        match.group("arch"),
        _version(match.group("ver")),
        _asset_from_json(item),
    )


def min_cuda_for(gpu_name: str) -> tuple[int, ...]:
    """RTX 50-series (Blackwell) needs a CUDA 12.8 or newer build."""
    if re.search(r"RTX\s*50\d{2}|Blackwell|\bB[12]00\b|\bGB[12]00\b", gpu_name, re.IGNORECASE):
        return BLACKWELL_MIN_CUDA
    return ()


def _arch_for(machine: str) -> str:
    return "arm64" if machine.lower() in ("arm64", "aarch64") else "x64"


def backend_order(hw: HardwareInfo, system: str) -> list[str]:
    """The builds to try, best first: CUDA (or ROCm) -> Vulkan -> CPU."""
    if system == "Darwin":
        return ["metal"]
    first = hw.backend
    if first in ("cuda",):
        return ["cuda", "vulkan", "cpu"]
    if first in ("rocm", "hip"):
        return ["rocm", "vulkan", "cpu"]
    if first == "vulkan":
        return ["vulkan", "cpu"]
    return ["cpu"]


def _pick_cuda(
    candidates: list[ParsedAsset], driver_cuda: str, minimum: tuple[int, ...]
) -> ParsedAsset | None:
    usable = [c for c in candidates if c.version >= minimum]
    if driver_cuda:
        limit = _version(driver_cuda)
        usable = [c for c in usable if c.version <= limit]
        return max(usable, key=lambda c: c.version, default=None)
    return min(usable, key=lambda c: c.version, default=None)  # unknown driver: most compatible


def find_llamacpp_assets(
    hw: HardwareInfo,
    release_json: dict,
    *,
    system: str | None = None,
    machine: str | None = None,
) -> list[EnginePlan]:
    """Choose llama.cpp builds for this hardware from release JSON, best first (fallbacks after)."""
    system = system or sys_platform_name()
    machine = machine or platform.machine()
    os_key, arch = _OS_KEY.get(system, "ubuntu"), _arch_for(machine)
    tag = str(release_json.get("tag_name", ""))
    items = release_json.get("assets", [])
    builds = [p for p in (parse_build_asset(i) for i in items) if p]
    cudart = [p for p in (parse_cudart_asset(i) for i in items) if p]
    plans: list[EnginePlan] = []
    for backend in backend_order(hw, system):
        candidates = [b for b in builds if (b.os_key, b.arch, b.backend) == (os_key, arch, backend)]
        if system == "Darwin":
            candidates = [b for b in builds if (b.os_key, b.arch) == (os_key, arch)]
        if not candidates:
            continue
        if backend == "cuda":
            plan = _cuda_plan(hw, candidates, cudart, tag, os_key, arch)
        else:
            chosen = max(candidates, key=lambda c: c.version)
            plan = EnginePlan(tag, backend, [chosen.asset], _reason(hw, backend, chosen))
        if plan is not None:
            plans.append(plan)
    return plans


def _cuda_plan(
    hw: HardwareInfo,
    candidates: list[ParsedAsset],
    cudart: list[tuple[str, str, tuple[int, ...], Asset]],
    tag: str,
    os_key: str,
    arch: str,
) -> EnginePlan | None:
    chosen = _pick_cuda(candidates, hw.cuda_version, min_cuda_for(hw.gpu_name))
    if chosen is None:
        return None
    assets = [chosen.asset]
    for c_os, c_arch, c_ver, asset in cudart:
        if (c_os, c_arch, c_ver) == (os_key, arch, chosen.version):
            assets.append(asset)
    version = ".".join(map(str, chosen.version))
    driver = f" (your driver supports CUDA {hw.cuda_version})" if hw.cuda_version else ""
    return EnginePlan(tag, "cuda", assets, f"NVIDIA GPU: the CUDA {version} build{driver}")


def _reason(hw: HardwareInfo, backend: str, chosen: ParsedAsset) -> str:
    names = {
        "vulkan": "the Vulkan build (works on most GPUs)",
        "cpu": "the CPU-only build (no GPU acceleration)",
        "metal": "the Apple Metal build",
        "rocm": "the AMD ROCm build",
    }
    return f"{hw.gpu_name or hw.vendor}: {names.get(backend, backend)}"


def find_llamacpp_asset(
    hw: HardwareInfo, release_json: dict, *, system: str | None = None, machine: str | None = None
) -> EnginePlan | None:
    """The single best build for this hardware, or None when the release has nothing suitable."""
    plans = find_llamacpp_assets(hw, release_json, system=system, machine=machine)
    return plans[0] if plans else None


# --------------------------------------------------------------------------- release lookup


def fetch_release(tag: str | None = None, *, http: httpx.Client | None = None) -> dict:
    """Read a release from the official repository: the pinned tag, else the default tag."""
    client = http or httpx.Client(timeout=30.0, headers={"Accept": "application/vnd.github+json"})
    wanted = tag or DEFAULT_LLAMACPP_TAG
    try:
        response = client.get(f"{GITHUB_API}/releases/tags/{wanted}")
    except httpx.HTTPError as exc:
        raise InstallError(
            f"Could not reach GitHub to look up llama.cpp release {wanted} ({exc}). "
            "Check your internet connection and run the command again."
        ) from exc
    if response.status_code == 404:
        raise InstallError(
            f"llama.cpp release '{wanted}' does not exist on GitHub. "
            "Check [local] llamacpp_tag in ~/.qedcode/config.toml, or clear it to use the default."
        )
    if response.status_code == 403:
        raise InstallError(
            "GitHub is limiting requests from this network right now. "
            "Wait a few minutes and try again."
        )
    if response.status_code >= 400:
        raise InstallError(
            f"GitHub answered HTTP {response.status_code} when looking up llama.cpp {wanted}. "
            "Try again later."
        )
    return response.json()


def newest_release_tag(*, http: httpx.Client | None = None) -> str:
    """The newest llama.cpp release tag that ships binaries (for `qed doctor --update-engine`)."""
    client = http or httpx.Client(timeout=30.0, headers={"Accept": "application/vnd.github+json"})
    try:
        response = client.get(f"{GITHUB_API}/releases", params={"per_page": 10})
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise InstallError(
            f"Could not look up the newest llama.cpp release ({exc}). Try again later."
        ) from exc
    for release in response.json():
        if any(parse_build_asset(a) for a in release.get("assets", [])):
            return str(release["tag_name"])
    raise InstallError("No llama.cpp release with downloadable builds was found.")


# --------------------------------------------------------------------------- installed engines


def engine_dir(tag: str, backend: str) -> Path:
    return bin_dir() / "llama.cpp" / tag / backend


def find_server_binary(directory: Path) -> Path | None:
    """Find llama-server(.exe) anywhere under `directory`."""
    names = ("llama-server.exe",) if sys.platform == "win32" else ("llama-server",)
    for name in names:
        for path in sorted(directory.rglob(name)):
            if path.is_file():
                return path
    return None


def load_installed(tag: str, backend: str) -> InstalledEngine | None:
    """Read an installed engine's manifest; None when it is not installed or damaged."""
    directory = engine_dir(tag, backend)
    try:
        data = json.loads((directory / ENGINE_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    binary = directory / str(data.get("binary", ""))
    if not binary.is_file():
        return None
    return InstalledEngine(tag, backend, directory, binary, str(data.get("version", "")))


def find_installed_engine(hw: HardwareInfo, tag: str, system: str | None = None):
    """The best already-installed build for this hardware at `tag`, or None."""
    for backend in backend_order(hw, system or sys_platform_name()):
        engine = load_installed(tag, backend)
        if engine is not None:
            return engine
    return None


def engine_env(engine: InstalledEngine) -> dict[str, str]:
    """Library search path so a build finds its shared libraries next to the binary."""
    folder = str(engine.bin_dir)
    if sys.platform == "darwin":
        return {"DYLD_LIBRARY_PATH": folder}
    if sys.platform.startswith("linux"):
        return {"LD_LIBRARY_PATH": folder}
    return {}


# --------------------------------------------------------------------------- downloading


def format_size(num_bytes: int) -> str:
    if num_bytes >= 1024**3:
        return f"{num_bytes / 1024**3:.1f} GB"
    return f"{max(num_bytes, 1) / 1024**2:.0f} MB"


def _check_official(asset: Asset) -> None:
    if not asset.url.startswith(OFFICIAL_DOWNLOAD_PREFIX):
        raise InstallError(
            f"Refusing to download {asset.name}: it is not from the official ggml-org/llama.cpp "
            "releases. QEDCode only installs official builds."
        )


def _download(
    asset: Asset, target: Path, http: httpx.Client, on_progress: ProgressFn | None
) -> None:
    sha = hashlib.sha256()
    done = 0
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with http.stream("GET", asset.url, follow_redirects=True) as response:
            if response.status_code >= 400:
                raise InstallError(
                    f"Downloading {asset.name} failed with HTTP {response.status_code}. "
                    "Try again later."
                )
            with target.open("wb") as handle:
                for chunk in response.iter_bytes(1024 * 256):
                    handle.write(chunk)
                    sha.update(chunk)
                    done += len(chunk)
                    if on_progress is not None:
                        on_progress(asset.name, done, asset.size_bytes)
    except httpx.HTTPError as exc:
        raise InstallError(
            f"Downloading {asset.name} stopped ({exc}). Check your internet connection and run "
            "the command again."
        ) from exc
    if asset.sha256 and sha.hexdigest() != asset.sha256:
        target.unlink(missing_ok=True)
        raise InstallError(
            f"The downloaded {asset.name} does not match its published checksum, so it was "
            "deleted. Run the command again; if it keeps happening, the download is being altered."
        )


def extract_archive(archive: Path, destination: Path) -> None:
    """Safely unpack a .zip or .tar.gz into `destination` (no path traversal)."""
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            for member in zf.infolist():
                target = (destination / member.filename).resolve()
                if not target.is_relative_to(root):
                    raise InstallError(
                        f"{archive.name} contains an unsafe path, so nothing was unpacked. "
                        "Try the download again later; if it keeps happening, report it."
                    )
            zf.extractall(destination)
        return
    with tarfile.open(archive, "r:gz") as tf:
        try:
            tf.extractall(destination, filter="data")
        except TypeError:  # Python without the extraction filters: check by hand
            for member in tf.getmembers():
                target = (destination / member.name).resolve()
                if not target.is_relative_to(root) or member.issym() or member.islnk():
                    if not (member.issym() and target.is_relative_to(root)):
                        raise InstallError(
                            f"{archive.name} contains an unsafe path, so nothing was unpacked. "
                            "Try the download again later; if it keeps happening, report it."
                        ) from None
            tf.extractall(destination)  # noqa: S202 - members were checked just above


def _make_executable(directory: Path) -> None:
    if sys.platform == "win32":
        return
    for path in directory.rglob("llama-*"):
        if path.is_file() and not path.suffix:
            path.chmod(path.stat().st_mode | 0o111)


def _install_plan(
    plan: EnginePlan, http: httpx.Client, on_progress: ProgressFn | None
) -> InstalledEngine:
    needed = plan.total_bytes * 3  # the archive, its unpacked files and some room
    free = shutil.disk_usage(bin_dir().parent if bin_dir().parent.exists() else Path.home()).free
    if needed and free < needed:
        raise InstallError(
            f"There is not enough free disk space for the {plan.backend} build: it needs about "
            f"{format_size(needed)} and {format_size(free)} is free. Free some space and try again."
        )
    destination = engine_dir(plan.tag, plan.backend)
    shutil.rmtree(destination, ignore_errors=True)
    tmp = bin_dir() / ".downloads"
    try:
        for asset in plan.assets:
            _check_official(asset)
            archive = tmp / asset.name
            _download(asset, archive, http, on_progress)
            extract_archive(archive, destination)
            archive.unlink(missing_ok=True)
        _make_executable(destination)
        binary = find_server_binary(destination)
        if binary is None:
            raise InstallError(
                f"The {plan.backend} download did not contain llama-server. "
                "This build is not usable on this computer."
            )
        engine = InstalledEngine(plan.tag, plan.backend, destination, binary)
        result = run([binary, "--version"], timeout=60, env=engine_env(engine), cwd=binary.parent)
        if not result.ok:
            raise InstallError(
                f"The {plan.backend} build of llama-server would not start "
                f"({(result.error or result.output)[:200] or 'no message'})."
            )
        engine.version = result.output.splitlines()[0] if result.output else plan.tag
        (destination / ENGINE_MANIFEST).write_text(
            json.dumps(
                {
                    "tag": plan.tag,
                    "backend": plan.backend,
                    "binary": binary.relative_to(destination).as_posix(),
                    "version": engine.version,
                    "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "assets": [a.name for a in plan.assets],
                },
                indent=1,
            ),
            encoding="utf-8",
        )
        return engine
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def download_llamacpp(
    plans: EnginePlan | list[EnginePlan],
    confirm: ConfirmFn,
    on_progress: ProgressFn | None = None,
    *,
    http: httpx.Client | None = None,
) -> InstalledEngine:
    """Ask, download and install the first build that works; fall back CUDA -> Vulkan -> CPU.

    `confirm` is asked once per build with its size; declining stops everything.
    """
    ordered = [plans] if isinstance(plans, EnginePlan) else list(plans)
    if not ordered:
        raise InstallError(
            "No llama.cpp build matches this computer in that release. Run `qed doctor`, or use "
            "Ollama or LM Studio instead (qed setup)."
        )
    client = http or httpx.Client(timeout=httpx.Timeout(30.0, read=120.0))
    failures: list[str] = []
    for plan in ordered:
        prompt = (
            f"Download the {plan.backend} build of llama.cpp {plan.tag} "
            f"({format_size(plan.total_bytes)}) from github.com/ggml-org/llama.cpp? {plan.reason}"
        )
        if not confirm(prompt):
            raise InstallDeclined(
                "Nothing was downloaded because you said no. Run `qed setup` again when you are "
                "ready, or point QEDCode at Ollama or LM Studio instead."
            )
        try:
            engine = _install_plan(plan, client, on_progress)
        except InstallError as exc:
            log.warning("llama.cpp %s build failed: %s", plan.backend, exc)
            failures.append(f"{plan.backend}: {exc}")
            continue
        if failures:
            log.info("Installed the %s build after: %s", plan.backend, "; ".join(failures))
        return engine
    raise InstallError(
        "No llama.cpp build could be installed. What went wrong: "
        + " | ".join(failures)
        + " Update your GPU driver, or use Ollama or LM Studio instead (qed setup)."
    )


# --------------------------------------------------------------------------- model downloads


def sha256_of(path: Path, chunk: int = 1024 * 1024) -> str:
    """SHA-256 of a file, read in chunks (model files are several GB)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def verify_model_file(entry: CatalogEntry, path: Path | None = None) -> bool:
    """True when the downloaded file exists and matches the catalog checksum (and size)."""
    path = path or local_path(entry)
    if not path.is_file():
        return False
    if entry.gguf.size_bytes and path.stat().st_size != entry.gguf.size_bytes:
        return False
    expected = entry.gguf.sha256
    return True if expected in ("<fill>", "<verify>") else sha256_of(path) == expected


def hf_hub_download(**kwargs) -> str:
    """huggingface_hub's download, imported on first use (the `download` extra)."""
    return load_extra("huggingface_hub", "download", "Downloading a model file").hf_hub_download(
        **kwargs
    )


def _progress_bar_class(callback: ProgressFn | None, name: str, total: int):
    from tqdm.auto import tqdm  # comes with huggingface_hub

    class _Bar(tqdm):
        def __init__(self, *args, **kwargs):
            kwargs["disable"] = True  # no terminal bar from here; the UI draws its own
            super().__init__(*args, **kwargs)
            self._qed_done = 0

        def update(self, n=1):
            self._qed_done += n or 0
            if callback is not None:
                callback(name, self._qed_done, int(self.total or total))
            return True

    return _Bar


def download_model(
    entry: CatalogEntry,
    confirm: ConfirmFn,
    on_progress: ProgressFn | None = None,
) -> Path:
    """Ask, then download a catalog model to ~/.qedcode/models/<id>/ and check its checksum.

    Safe to call again after an interruption: huggingface_hub resumes the partial file.
    """
    if not entry.verified:
        raise InstallError(
            f"The catalog entry for {entry.id} still has unverified values "
            f"({', '.join(entry.unverified_fields())}), so it cannot be downloaded safely. "
            "Choose another model with `qed models list`."
        )
    target = local_path(entry)
    if verify_model_file(entry, target):
        return target  # already here and intact: nothing to download
    try:  # before asking: a yes that cannot be honoured would be a wasted question
        load_extra("huggingface_hub", "download", "Downloading a model file")
    except MissingExtra as exc:
        raise InstallError(str(exc)) from exc
    size = entry.gguf.size_bytes or int((entry.weights_gb or 0) * 1e9)
    prompt = (
        f"Download {entry.display} ({format_size(size)}) from huggingface.co/{entry.gguf.repo}? "
        f"It is saved under {target.parent}."
    )
    if not confirm(prompt):
        raise InstallDeclined(
            f"Nothing was downloaded because you said no. Run `qed models download {entry.id}` "
            "when you are ready."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(target.parent).free
    needed = int(size * 1.05)
    if free < needed:
        raise InstallError(
            f"There is not enough free disk space for {entry.display}: it needs about "
            f"{format_size(needed)} and {format_size(free)} is free in {target.parent}. "
            "Free some space, or remove a model you do not use (`qed models remove <id>`)."
        )
    try:
        hf_hub_download(
            repo_id=entry.gguf.repo,
            filename=entry.gguf.file,
            revision=entry.gguf.revision or None,
            local_dir=str(target.parent),
            tqdm_class=_progress_bar_class(on_progress, entry.gguf.file, size),
        )
    except Exception as exc:  # noqa: BLE001 - huggingface_hub raises many kinds of errors
        raise InstallError(
            f"Downloading {entry.display} stopped ({type(exc).__name__}: {str(exc)[:160]}). "
            f"Check your internet connection and run `qed models download {entry.id}` again; "
            "the download resumes where it stopped."
        ) from exc
    if not target.is_file():
        raise InstallError(f"The download finished but {target} is missing. Run the command again.")
    if not verify_model_file(entry, target):
        target.unlink(missing_ok=True)
        raise InstallError(
            f"The downloaded {entry.gguf.file} does not match the catalog checksum, so it was "
            f"deleted. Run `qed models download {entry.id}` again; if it keeps failing, the "
            "catalog entry may be out of date (please report it)."
        )
    return target


def remove_model(entry: CatalogEntry, confirm: ConfirmFn) -> bool:
    """Delete a downloaded model after asking. Returns False when there was nothing to remove."""
    folder = models_dir() / entry.id
    if not folder.exists():
        return False
    size = sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())
    if not confirm(f"Delete {entry.display} ({format_size(size)}) from {folder}?"):
        raise InstallDeclined("Nothing was deleted because you said no.")
    shutil.rmtree(folder)
    return True
