"""`qed doctor`: check everything QEDCode depends on and say how to fix what is wrong.

One Check per thing (git, the project's Python, pytest, ruff, hardware, the engine build, the model,
the model server, disk space, leftovers). Every warning or failure carries a fix in plain words.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from qedcode.config import GlobalConfig, ProjectConfig, load_project, logs_dir, qedcode_home
from qedcode.hardware.detect import HardwareInfo, current_hardware
from qedcode.project.detect import find_python, find_test_files, probe_python
from qedcode.project.detect_js import choose_language, detect_js
from qedcode.runtime import installer
from qedcode.vcs import worktree
from qedcode.vcs.git import has_commits, is_git_repo
from qedcode.verify.run import run
from qedcode.verify.sandbox import DockerSandbox

Status = Literal["ok", "warn", "fail"]
GIB = 1024**3


@dataclass
class Check:
    name: str
    status: Status
    detail: str
    fix: str = ""


def _ok(name: str, detail: str) -> Check:
    return Check(name, "ok", detail)


def _bad(name: str, detail: str, fix: str, status: Status = "fail") -> Check:
    return Check(name, status, detail, fix)


# --------------------------------------------------------------------------- individual checks


def check_git() -> Check:
    result = run(["git", "--version"], timeout=15)
    if not result.ok:
        return _bad(
            "git",
            "git could not be run",
            "Install git (https://git-scm.com) and make sure the `git` command works in a terminal.",
        )
    return _ok("git", result.output.splitlines()[0])


def check_project(root: Path) -> Check:
    if not is_git_repo(root):
        return _bad(
            "project",
            f"{root} is not a git repository",
            "Run `qed` in the folder (it offers to set git up), or `git init` and make a first commit.",
        )
    if not has_commits(root):
        return _bad(
            "project",
            "the repository has no commits yet",
            "Make a first commit: `git add -A && git commit -m start`.",
        )
    saved = load_project(root)
    if not saved.confirmed:
        return _bad(
            "project",
            "git is fine, but the project settings were never confirmed",
            "Run `qed` once in this folder and confirm the detected Python and tests.",
            "warn",
        )
    return _ok("project", f"git repository with commits; settings confirmed ({root.name})")


def project_python(root: Path) -> tuple[str, ProjectConfig]:
    saved = load_project(root)
    if saved.python:
        return saved.python, saved
    python, _source, _notes = find_python(root)
    return python, saved


def check_python(
    root: Path, probe: Callable[[str, Path], tuple[str, str]] = probe_python
) -> list[Check]:
    """The project's Python, and pytest inside it (two checks)."""
    python, _ = project_python(root)
    version, pytest_version = probe(python, root)
    if not version:
        return [
            _bad(
                "python",
                f"{python} could not be run",
                "Pick a working Python: `uv sync` in the project, or set `python` in .qed/project.toml.",
            ),
            _bad("pytest", "unknown (Python could not be run)", "Fix the Python first."),
        ]
    out = [_ok("python", f"{python} (Python {version})")]
    if pytest_version:
        out.append(_ok("pytest", f"pytest {pytest_version} in that Python"))
    else:
        out.append(
            _bad(
                "pytest",
                f"pytest is not installed in {python}",
                f"Install it there: `{python} -m pip install pytest` (or `uv pip install --python {python} pytest`); `qed` also offers to do it.",
            )
        )
    return out


def project_language(root: Path) -> str:
    """'python' or 'javascript': the confirmed project setting, else what detection finds."""
    saved = load_project(root)
    if saved.confirmed:
        return saved.language
    return choose_language(root, find_test_files)


def check_javascript(root: Path) -> list[Check]:
    """Node.js and the test runner of a JavaScript/TypeScript project (two checks)."""
    found = detect_js(root)
    out: list[Check] = []
    if not found.node_version:
        out.append(
            _bad(
                "node",
                "Node.js was not found or could not be run",
                "Install Node.js 20 or newer (https://nodejs.org) and make sure `node` works "
                "in a terminal.",
            )
        )
    else:
        out.append(_ok("node", f"Node.js {found.node_version} ({found.node_path})"))
    runner = found.runner
    if found.runner_ok:
        label = f"{runner} {found.runner_version}".strip() if runner != "node" else "node:test"
        out.append(_ok("test runner", f"{label} ({found.package_manager or 'npm'})"))
    elif runner == "node":
        out.append(_bad("test runner", "node:test needs a working Node.js", "Fix Node.js first."))
    else:
        manager = found.package_manager or "npm"
        out.append(
            _bad(
                "test runner",
                f"{runner} is in package.json but not installed in node_modules",
                f"Run `{manager} install` in the project (QEDCode never installs Node packages).",
            )
        )
    return out


def check_ruff() -> Check:
    result = run([sys.executable, "-m", "ruff", "--version"], timeout=30)
    if result.ok:
        return _ok("ruff", result.output.splitlines()[0])
    return _bad(
        "ruff",
        "ruff could not be run (QEDCode uses it for the lint check)",
        "Reinstall QEDCode so its dependencies are complete: `uv tool install --force qedcode`. Until then lint is skipped.",
        "warn",
    )


def check_hardware(hw: HardwareInfo) -> Check:
    if hw.vendor == "cpu" or hw.vram_gb <= 0:
        return _bad(
            "hardware",
            "no usable GPU memory found: models will run on the CPU, which is slow",
            "If you have a GPU, set its vendor and VRAM by hand in `qed setup`, update the GPU driver, or use the Fast speed.",
            "warn",
        )
    return _ok(
        "hardware",
        f"{hw.gpu_name or hw.vendor}, {hw.vram_gb:.1f} GB usable ({hw.backend} build), found by {hw.source}",
    )


def check_engine(config: GlobalConfig, hw: HardwareInfo) -> Check:
    if config.local.backend != "llamacpp" or config.engine.mode.value == "cloud":
        return _ok("engine build", "not needed (this setup does not use llama.cpp)")
    tag = config.local.llamacpp_tag or installer.DEFAULT_LLAMACPP_TAG
    engine = installer.find_installed_engine(hw, tag)
    if engine is None:
        return _bad(
            "engine build",
            f"the llama.cpp build {tag} is not installed",
            "Run `qed setup` (it shows the size and asks before downloading), or use Ollama or LM Studio instead.",
        )
    result = run(
        [engine.binary, "--version"],
        timeout=30,
        env=installer.engine_env(engine),
        cwd=engine.bin_dir,
    )
    if not result.ok:
        return _bad(
            "engine build",
            f"llama-server ({engine.backend} build {tag}) would not start",
            "Update your GPU driver, or run `qed doctor --update-engine`; `qed setup` can also fall back to the Vulkan or CPU build.",
        )
    return _ok(
        "engine build", f"llama.cpp {tag}, {engine.backend} build ({engine.version or 'runs'})"
    )


def _model_file_check(config: GlobalConfig) -> Check:
    from qedcode.runtime.catalog import CatalogError, load_catalog, local_path

    model = config.local.model
    if model.lower().endswith(".gguf"):
        path = Path(model).expanduser()
        if path.is_file():
            return _ok("model file", f"{path} ({path.stat().st_size / GIB:.1f} GB)")
        return _bad(
            "model file",
            f"{path} does not exist",
            "Check the path in ~/.qedcode/config.toml or run `qed setup`.",
        )
    try:
        entry = load_catalog().require(model)
    except CatalogError:
        return _bad(
            "model file",
            f"'{model}' is not a model QEDCode knows",
            "Run `qed models list` and `qed models use <id>`.",
        )
    path = local_path(entry)
    if not path.is_file():
        return _bad(
            "model file",
            f"{entry.display} is not downloaded",
            f"Run `qed models download {entry.id}` (it shows the size and asks first).",
        )
    expected = entry.gguf.size_bytes
    if expected and path.stat().st_size != expected:
        return _bad(
            "model file",
            f"{path.name} has the wrong size ({path.stat().st_size} bytes, expected {expected})",
            f"Remove it and download again: `qed models remove {entry.id}` then `qed models download {entry.id}`.",
        )
    return _ok(
        "model file",
        f"{entry.display}, {path.stat().st_size / GIB:.2f} GB, size matches the catalog",
    )


def check_model(config: GlobalConfig) -> Check:
    """The configured model exists (a file, an Ollama model, an LM Studio server or a cloud key)."""
    if config.engine.mode.value == "cloud":
        from qedcode.runtime.secrets import env_var_for, get_api_key, needs_key, provider_of

        model = config.cloud.model
        if not model:
            return _bad(
                "model", "no cloud model is set", "Run `qed setup` and pick a provider and model."
            )
        provider = provider_of(model)
        if needs_key(provider) and get_api_key(provider) is None:
            return _bad(
                "model",
                f"there is no API key for {provider}",
                f"Run `qed setup` to save one in the system keychain, or set {env_var_for(provider)}.",
            )
        return _ok("model", f"cloud model {model} (key found)")
    backend = config.local.backend
    if backend == "ollama":
        from qedcode.runtime.client import ollama_base_url
        from qedcode.runtime.ollama import OllamaClient

        client = OllamaClient(config.local.model, base_url=ollama_base_url())
        try:
            if not client.is_running():
                return _bad(
                    "model",
                    "Ollama is not running",
                    "Start Ollama (open the app or run `ollama serve`).",
                )
            names = client.list_models()
        except Exception:  # noqa: BLE001
            return _bad(
                "model", "Ollama did not answer", "Restart Ollama and run `qed doctor` again."
            )
        finally:
            client.close()
        if config.local.model not in names:
            return _bad(
                "model",
                f"Ollama has no model '{config.local.model}'",
                f"Run `ollama pull {config.local.model}`.",
            )
        return _ok("model", f"Ollama model {config.local.model}")
    if backend == "openai_compat":
        from qedcode.runtime.openai_compat import OpenAICompatClient

        if not config.local.base_url:
            return _bad(
                "model",
                "no base_url is set for the OpenAI-compatible server",
                "Set [local] base_url in ~/.qedcode/config.toml, e.g. http://127.0.0.1:1234/v1.",
            )
        client = OpenAICompatClient(config.local.model, config.local.base_url)
        healthy = client.health()
        client.close()
        if not healthy:
            return _bad(
                "model",
                f"nothing answers at {config.local.base_url}",
                "Start the server (for LM Studio: load a model and start the local server).",
            )
        return _ok("model", f"{config.local.model} at {config.local.base_url}")
    return _model_file_check(config)


def check_server(config: GlobalConfig) -> Check:
    """The shared llama-server: running and healthy, or simply not started yet."""
    if config.local.backend != "llamacpp" or config.engine.mode.value == "cloud":
        return _ok("model server", "not used by this setup")
    from qedcode.runtime.server_manager import ServerManager

    manager = ServerManager()
    info = manager.info()
    if info is None:
        return _ok(
            "model server", "not running (it starts when you run a task and stops when idle)"
        )
    if manager.is_healthy(info):
        idle = manager.idle_seconds() or 0
        return _ok(
            "model server",
            f"healthy: {info.model_id} on port {info.port} (pid {info.pid}, idle {idle:.0f}s)",
        )
    return _bad(
        "model server",
        f"the model server (pid {info.pid}) is running but not answering on port {info.port}",
        "It may still be loading; wait a minute. If it stays stuck, stop it with `qed models use` or kill that process and try again.",
        "warn",
    )


def check_disk(config: GlobalConfig, root: Path | None = None) -> Check:
    """Free disk space where models are stored (QEDCODE_HOME), against what the model needs."""
    from qedcode.runtime.catalog import CatalogError, load_catalog, local_path

    home = qedcode_home()
    probe = home if home.exists() else home.parent if home.parent.exists() else Path.home()
    free = shutil.disk_usage(probe).free
    need = 0
    if config.local.backend == "llamacpp" and not config.local.model.lower().endswith(".gguf"):
        try:
            entry = load_catalog().require(config.local.model)
            if not local_path(entry).is_file():
                need = int(entry.gguf.size_bytes * 1.1)
        except CatalogError:
            pass
    if need and free < need:
        return _bad(
            "disk space",
            f"{free / GIB:.1f} GB free in {probe}, but the model needs about {need / GIB:.1f} GB",
            "Free some space, or move QEDCODE_HOME to a bigger drive (set the QEDCODE_HOME environment variable).",
        )
    if free < 2 * GIB:
        return _bad(
            "disk space",
            f"only {free / GIB:.1f} GB free in {probe}",
            "Free some disk space: models are several GB.",
            "warn",
        )
    return _ok("disk space", f"{free / GIB:.1f} GB free in {probe}")


def check_sandbox(root: Path) -> Check:
    """The sealed test box: off is fine; on needs Docker and a built image that works."""
    from qedcode.engine import sandbox_setup

    state = sandbox_setup.status(root)
    if state.mode == "off":
        return _ok(
            "sealed test box",
            "off: tests run on this computer (turn on with `qed sandbox setup`, needs Docker)",
        )
    if not state.docker_ready:
        return _bad("sealed test box", "on, but Docker is not usable", state.docker_message)
    if not state.image_built:
        return _bad(
            "sealed test box",
            f"on, but the image '{state.image}' is not built",
            "Run `qed sandbox setup` (it explains what it downloads and asks first), "
            "or `qed sandbox off`.",
        )
    works, why = DockerSandbox(image=state.image).smoke_test()
    if not works:
        return _bad("sealed test box", "the image starts but cannot run tests", why)
    return _ok("sealed test box", f"on and working (image {state.image}, network off)")


def check_leftovers(root: Path) -> Check:
    left = worktree.list_worktrees(root) if root.exists() else []
    if left:
        return _bad(
            "leftover copies",
            f"{len(left)} leftover worktree folder(s) in .qed/worktrees (a run crashed)",
            "Run `git worktree prune`, then delete the folder .qed/worktrees/.",
            "warn",
        )
    return _ok("leftover copies", "none")


def check_logs() -> Check:
    folder = logs_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return _bad(
            "logs",
            f"cannot write to {folder} ({exc})",
            "Check the folder's permissions, or set QEDCODE_HOME to a folder you can write to.",
            "warn",
        )
    return _ok("logs", f"{folder} (rotated at 5 MB, 3 files kept)")


# --------------------------------------------------------------------------- the whole doctor


def run_doctor(
    root: Path | str,
    config: GlobalConfig,
    *,
    hw: HardwareInfo | None = None,
) -> list[Check]:
    """Run every check in a sensible order. Never raises."""
    folder = Path(root)
    hardware = hw or current_hardware()
    steps: list[Callable[[], Check | list[Check]]] = [
        check_git,
        lambda: check_project(folder),
        lambda: (
            check_javascript(folder)
            if project_language(folder) == "javascript"
            else check_python(folder)
        ),
        check_ruff,
        lambda: check_hardware(hardware),
        lambda: check_engine(config, hardware),
        lambda: check_model(config),
        lambda: check_server(config),
        lambda: check_disk(config, folder),
        lambda: check_sandbox(folder),
        lambda: check_leftovers(folder),
        check_logs,
    ]
    checks: list[Check] = []
    for step in steps:
        try:
            result = step()
        except Exception as exc:  # noqa: BLE001 - the doctor itself must never crash
            checks.append(
                _bad(
                    "doctor",
                    f"a check crashed ({type(exc).__name__}: {exc})",
                    "Please report this; see the log.",
                    "warn",
                )
            )
            continue
        checks.extend(result if isinstance(result, list) else [result])
    return checks


def update_engine(
    config: GlobalConfig,
    hw: HardwareInfo,
    confirm: Callable[[str], bool],
    on_progress: installer.ProgressFn | None = None,
    *,
    fetch_newest: Callable[[], str] = installer.newest_release_tag,
    fetch_release: Callable[[str | None], dict] = lambda tag: installer.fetch_release(tag),
    download: Callable[..., installer.InstalledEngine] = installer.download_llamacpp,
) -> str:
    """Move to the newest llama.cpp build (after asking). Returns a message; the caller saves
    `config.local.llamacpp_tag` when the message starts with 'Updated'."""
    current = config.local.llamacpp_tag or installer.DEFAULT_LLAMACPP_TAG
    newest = fetch_newest()
    if newest == current and installer.find_installed_engine(hw, current) is not None:
        return f"llama.cpp {current} is already the newest build."
    plans = installer.find_llamacpp_assets(hw, fetch_release(newest))
    if not plans:
        return f"The newest llama.cpp release ({newest}) has no build for this computer; staying on {current}."
    engine = download(plans, confirm, on_progress)
    config.local.llamacpp_tag = newest
    return f"Updated llama.cpp from {current} to {newest} ({engine.backend} build)."


def summarize(checks: list[Check]) -> tuple[int, int, int]:
    """(ok, warnings, failures)."""
    return (
        sum(c.status == "ok" for c in checks),
        sum(c.status == "warn" for c in checks),
        sum(c.status == "fail" for c in checks),
    )
