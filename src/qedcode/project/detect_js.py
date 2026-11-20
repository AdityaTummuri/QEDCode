"""Detect a JavaScript or TypeScript project (AGENTS.md section 15.3): language, runner, tests.

Nothing is installed or downloaded: if the test runner is missing from `node_modules` the result
says so and what to do (QEDCode never runs `npm install`).
"""

from __future__ import annotations

import json
import os
import posixpath
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from qedcode.config import JS_PROTECTED, ProjectConfig
from qedcode.project.paths import JS_SUFFIXES, is_js_path
from qedcode.verify.run import RunResult, run

MIN_NODE_MAJOR = 20
MAX_FILES_SCANNED = 5000
SKIP_DIRS = {
    ".git", ".qed", "node_modules", "dist", "build", "coverage", ".next", ".nuxt", ".turbo",
    ".cache", "out", ".venv", "venv", "__pycache__",
}  # fmt: skip
PYTHON_MARKERS = (
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "pytest.ini",
    "tox.ini",
)
LOCKFILES = {
    "pnpm-lock.yaml": "pnpm",
    "yarn.lock": "yarn",
    "bun.lockb": "bun",
    "bun.lock": "bun",
    "package-lock.json": "npm",
}


def read_package_json(root: Path | str) -> dict:
    try:
        data = json.loads((Path(root) / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _is_js_test_name(name: str) -> bool:
    return ".test." in name or ".spec." in name


def find_js_test_files(root: Path | str) -> list[str]:
    """Repo-relative JS/TS test files: *.test.*, *.spec.*, files in __tests__, tests/ or test/."""
    root = Path(root)
    found: list[str] = []
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        parts = set(rel_dir.split("/")) if rel_dir != "." else set()
        in_test_dir = bool(parts & {"__tests__", "tests", "test"})
        for name in sorted(filenames):
            scanned += 1
            if scanned > MAX_FILES_SCANNED:
                return sorted(found)
            if not is_js_path(name) or name.endswith(".d.ts"):
                continue
            if _is_js_test_name(name) or in_test_dir:
                found.append((Path(dirpath) / name).relative_to(root).as_posix())
    return sorted(found)


def choose_language(root: Path | str, python_tests: Callable[[Path], list[str]]) -> str:
    """'javascript' for a package.json project without a Python project beside it, else 'python'.

    A folder with both is Python unless it has JS tests and no Python tests."""
    root = Path(root)
    if not (root / "package.json").is_file():
        return "python"
    has_python = any((root / marker).exists() for marker in PYTHON_MARKERS)
    if not has_python and not python_tests(root):
        return "javascript"
    if not python_tests(root) and find_js_test_files(root):
        return "javascript"
    return "python" if has_python else "javascript"


def resolve_js_import(test: str, spec: str, sources: set[str]) -> str | None:
    """The changed source file a relative import in `test` points at, or None.

    `./a.js` may be `a.ts` (TypeScript ESM style) and `./dir` may be `dir/index.*`."""
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(test), spec))
    cut = joined
    for suffix in JS_SUFFIXES:
        if joined.endswith(suffix):
            cut = joined[: -len(suffix)]
            break
    for source in sources:
        stem = source
        for suffix in JS_SUFFIXES:
            if source.endswith(suffix):
                stem = source[: -len(suffix)]
                break
        if stem == cut or stem == f"{cut}/index":
            return source
    return None


def package_manager_for(root: Path | str, package: dict | None = None) -> str:
    """npm, pnpm, yarn or bun, from the packageManager field or the lock file."""
    root = Path(root)
    data = package if package is not None else read_package_json(root)
    declared = str(data.get("packageManager", "")).split("@", 1)[0]
    if declared in ("npm", "pnpm", "yarn", "bun"):
        return declared
    for lock, manager in LOCKFILES.items():
        if (root / lock).exists():
            return manager
    return "npm" if data else ""


def runner_for(package: dict) -> str:
    """vitest, jest or node, from the dependencies and the `test` script."""
    deps: dict = {}
    for key in ("dependencies", "devDependencies"):
        value = package.get(key)
        if isinstance(value, dict):
            deps.update(value)
    script = str((package.get("scripts") or {}).get("test", "")) if package else ""
    if "vitest" in deps or "vitest" in script:
        return "vitest"
    if "jest" in deps or "jest" in script or "ts-jest" in deps:
        return "jest"
    return "node"


def runner_binary(root: Path | str, runner: str) -> Path | None:
    """`node_modules/.bin/<runner>` (the .cmd shim on Windows), or None when it is not installed."""
    bin_dir = Path(root) / "node_modules" / ".bin"
    for name in (runner, f"{runner}.cmd"):
        candidate = bin_dir / name
        if candidate.exists():
            return candidate
    return None


def version_tuple(text: str) -> tuple[int, int]:
    """(major, minor) of '22.18.0' (zeros when it cannot be read)."""
    parts = text.strip().lstrip("v").split(".")
    numbers = [int(p) if p.isdigit() else 0 for p in parts[:2]]
    return (numbers[0] if numbers else 0, numbers[1] if len(numbers) > 1 else 0)


@dataclass
class JsDetection:
    package_manager: str
    runner: str
    runner_ok: bool
    runner_version: str = ""
    node_path: str = ""
    node_version: str = ""
    typescript: bool = False
    test_files: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _probe(args: list[str], cwd: Path) -> RunResult:
    return run(args, cwd=cwd, timeout=30)


def detect_js(
    root: Path | str,
    *,
    which: Callable[[str], str | None] = shutil.which,
    probe: Callable[[list[str], Path], RunResult] = _probe,
) -> JsDetection:
    """What is known about a JavaScript/TypeScript project (nothing is installed or run twice)."""
    root = Path(root)
    package = read_package_json(root)
    runner = runner_for(package)
    manager = package_manager_for(root, package)
    notes: list[str] = []
    node = which("node") or ""
    node_version = ""
    if not node:
        notes.append("Node.js was not found on your PATH, so tests cannot run. Install Node 20+.")
    else:
        result = probe([node, "--version"], root)
        node_version = result.stdout.strip().lstrip("v") if result.ok else ""
        if not node_version:
            notes.append(f"Could not run {node}. Check your Node.js installation.")
        elif version_tuple(node_version)[0] < MIN_NODE_MAJOR:
            notes.append(
                f"Node {node_version} is too old; QEDCode needs Node {MIN_NODE_MAJOR} or newer."
            )
    runner_version = ""
    runner_ok = bool(node_version)
    if runner != "node":
        binary = runner_binary(root, runner)
        if binary is None:
            runner_ok = False
            notes.append(
                f"{runner} is in package.json but not installed in node_modules. Run "
                f"`{manager or 'npm'} install` yourself (QEDCode never installs Node packages), "
                "then try again."
            )
        else:
            version = probe([str(binary), "--version"], root)
            runner_version = (
                version.stdout.strip().splitlines()[0] if version.ok and version.stdout else ""
            )
    files = find_js_test_files(root)
    if not files:
        notes.append(
            "No test files were found (*.test.js, *.spec.ts, ...). "
            "QEDCode will write a test for the task."
        )
    typescript = (root / "tsconfig.json").is_file() or any(
        f.endswith((".ts", ".tsx", ".mts", ".cts")) for f in files
    )
    if runner == "node" and typescript and node_version and version_tuple(node_version) < (22, 18):
        notes.append(
            "TypeScript tests with the built-in runner need Node 22.18 or newer; "
            "use Vitest or Jest, or upgrade Node."
        )
    return JsDetection(
        package_manager=manager,
        runner=runner,
        runner_ok=runner_ok,
        runner_version=runner_version,
        node_path=node,
        node_version=node_version,
        typescript=typescript,
        test_files=files,
        notes=notes,
    )


def js_config(detection: JsDetection) -> ProjectConfig:
    """The project config for a detected JS/TS project (the user confirms it on first run)."""
    return ProjectConfig(
        language="javascript",
        python="",
        test_command=[],
        test_runner=detection.runner,  # type: ignore[arg-type]
        package_manager=detection.package_manager,
        protected=list(JS_PROTECTED),
    )


__all__ = [
    "JS_SUFFIXES",
    "JsDetection",
    "choose_language",
    "detect_js",
    "find_js_test_files",
    "js_config",
    "package_manager_for",
    "read_package_json",
    "resolve_js_import",
    "runner_binary",
    "runner_for",
]
