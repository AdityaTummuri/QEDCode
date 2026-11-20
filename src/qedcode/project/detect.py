"""Find a project's Python and tests, show them, and remember the user's answer.

Detection only reads: it never creates a virtual environment, installs anything or writes to the
project (installing pytest is a separate, ask-first function). Interpreter order (section 12):
.venv/, venv/, an active VIRTUAL_ENV, then python3 and python on PATH. When a uv.lock exists but
there is no .venv, the result says to run `uv sync`: `uv run` would create an environment inside
the user's project, which QEDCode does not do on its own.
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from qedcode.config import ProjectConfig, load_project, project_config_path, save_project
from qedcode.project.detect_js import choose_language, detect_js, find_js_test_files, js_config
from qedcode.vcs.git import git_out, has_commits, is_git_repo
from qedcode.verify.run import RunResult, run

SKIP_DIRS = {
    ".git",
    ".qed",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".tox",
    "build",
    "dist",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "site-packages",
    ".eggs",
}
MAX_FILES_SCANNED = 5000
PROBE_SCRIPT = (
    "import sys\n"
    "print(sys.version.split()[0])\n"
    "try:\n"
    "    import pytest\n"
    "    print(pytest.__version__)\n"
    "except Exception:\n"
    "    print('NOPYTEST')\n"
)
ProbeFn = Callable[[list[str], Path], RunResult]


def _default_probe(args: list[str], cwd: Path) -> RunResult:
    return run(args, cwd=cwd, timeout=30)


@dataclass
class ProjectDetection:
    """Everything the first-run screen shows, and the config the user is asked to confirm."""

    root: Path
    python: str
    python_source: str
    python_version: str = ""
    pytest_ok: bool = False
    pytest_version: str = ""
    test_files: list[str] = field(default_factory=list)
    test_count: int = 0
    config: ProjectConfig = field(default_factory=ProjectConfig)
    notes: list[str] = field(default_factory=list)
    language: str = "python"  # "javascript" includes TypeScript
    runner: str = "pytest"
    runner_ok: bool = True
    runner_version: str = ""
    node_version: str = ""

    def describe(self) -> str:
        """What was found, as the first-run screen shows it (JavaScript/TypeScript projects)."""
        runner = f"{self.runner} {self.runner_version}".strip()
        return (
            f"  Node.js: {self.node_version or 'not found'}\n"
            f"  Tests: {runner}{'' if self.runner_ok else ' (not available)'}; "
            f"{self.test_count} tests in {len(self.test_files)} files\n"
        )


@dataclass
class FirstRun:
    """What `qed` learns about a folder before its first task (UI-agnostic)."""

    root: Path
    is_git_repo: bool
    has_commits: bool
    already_confirmed: bool
    config: ProjectConfig
    detection: ProjectDetection | None
    qed_in_gitignore: bool
    problems: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return self.is_git_repo and self.has_commits and self.already_confirmed


# --------------------------------------------------------------------------- interpreter


def venv_python(venv: Path, system: str) -> Path | None:
    """The interpreter inside a virtual environment folder, or None if it has none."""
    candidates = (
        [venv / "Scripts" / "python.exe"]
        if system == "Windows"
        else [venv / "bin" / "python", venv / "bin" / "python3"]
    )
    return next((c for c in candidates if c.exists()), None)


def find_python(
    root: Path,
    *,
    env: Mapping[str, str] | None = None,
    system: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> tuple[str, str, list[str]]:
    """Choose the interpreter that runs this project's tests: (path, source, notes)."""
    env = os.environ if env is None else env
    system = system or {"win32": "Windows", "darwin": "Darwin"}.get(sys.platform, "Linux")
    notes: list[str] = []
    for folder in (".venv", "venv"):
        found = venv_python(root / folder, system)
        if found:
            return str(found), f"{folder}/", notes
    active = env.get("VIRTUAL_ENV")
    if active:
        found = venv_python(Path(active), system)
        if found:
            return str(found), "the active virtual environment", notes
    if (root / "uv.lock").exists():
        notes.append(
            "A uv.lock file was found but there is no .venv folder. Run `uv sync` in the project "
            "so its packages are installed, then run QEDCode again; QEDCode does not create "
            "environments by itself."
        )
    for name in ("python3", "python"):
        path = which(name)
        if path:
            notes.append(f"No project environment was found, so the {name} on your PATH is used.")
            return path, f"{name} on PATH", notes
    notes.append("No Python was found on your PATH; using the one that runs QEDCode.")
    return sys.executable, "the Python running QEDCode", notes


def probe_python(python: str, root: Path, probe: ProbeFn = _default_probe) -> tuple[str, str]:
    """Return (python version, pytest version); pytest version is '' when pytest is missing."""
    result = probe([python, "-c", PROBE_SCRIPT], root)
    if not result.ok:
        return "", ""
    lines = result.stdout.strip().splitlines()
    version = lines[0].strip() if lines else ""
    pytest_version = lines[1].strip() if len(lines) > 1 and lines[1].strip() != "NOPYTEST" else ""
    return version, pytest_version


# --------------------------------------------------------------------------- tests


def find_test_files(root: Path, language: str = "python") -> list[str]:
    """Repo-relative paths of the project's test files (not in venvs or build folders).

    Python: test_*.py and *_test.py. JavaScript/TypeScript: *.test.*, *.spec.*, __tests__/."""
    if language == "javascript":
        return find_js_test_files(root)
    found: list[str] = []
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            scanned += 1
            if scanned > MAX_FILES_SCANNED:
                return sorted(found)
            if name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py")):
                found.append((Path(dirpath) / name).relative_to(root).as_posix())
    return sorted(found)


JS_TEST_CALL = re.compile(r"^\s*(?:it|test)(?:\.(?:only|skip|todo))?\s*\(", re.MULTILINE)


def count_js_tests(root: Path, files: list[str]) -> int:
    """Estimate the number of JS/TS tests by counting `it(` and `test(` calls (nothing is run)."""
    total = 0
    for rel in files:
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        total += len(JS_TEST_CALL.findall(text))
    return total


def count_tests(root: Path, files: list[str]) -> int:
    """Estimate how many tests there are by reading the files (nothing is imported or run)."""
    total = 0
    for rel in files:
        try:
            tree = ast.parse((root / rel).read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, OSError, ValueError):
            continue
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
                "test"
            ):
                total += 1
            elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                total += sum(
                    1
                    for item in node.body
                    if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
                    and item.name.startswith("test")
                )
    return total


# --------------------------------------------------------------------------- detection


def detect_project(
    root: Path | str,
    *,
    env: Mapping[str, str] | None = None,
    system: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    probe: ProbeFn = _default_probe,
) -> ProjectDetection:
    """Detect the interpreter, pytest and test files for `root` and build a ProjectConfig.

    A `package.json` project without a Python project beside it is detected as JavaScript."""
    root = Path(root)
    if choose_language(root, find_test_files) == "javascript":
        js = detect_js(root, which=which)
        return ProjectDetection(
            root=root,
            python="",
            python_source="",
            test_files=js.test_files,
            test_count=count_js_tests(root, js.test_files),
            config=js_config(js),
            notes=js.notes,
            language="javascript",
            runner=js.runner,
            runner_ok=js.runner_ok,
            runner_version=js.runner_version,
            node_version=js.node_version,
        )
    python, source, notes = find_python(root, env=env, system=system, which=which)
    py_version, pytest_version = probe_python(python, root, probe)
    if not py_version:
        notes.append(f"Could not run {python}. Pick another Python when asked to confirm.")
    elif not pytest_version:
        notes.append(
            f"pytest is not installed in {python}. QEDCode can install it there if you agree "
            "(it asks first); until then tests cannot run."
        )
    files = find_test_files(root)
    if not files:
        notes.append(
            "No test files were found (test_*.py). QEDCode will write a test for the task."
        )
    config = ProjectConfig(python=python)
    return ProjectDetection(
        root=root,
        python=python,
        python_source=source,
        python_version=py_version,
        pytest_ok=bool(pytest_version),
        pytest_version=pytest_version,
        test_files=files,
        test_count=count_tests(root, files),
        config=config,
        notes=notes,
    )


def install_pytest(
    python: str,
    root: Path | str,
    confirm: Callable[[str], bool],
    *,
    runner: Callable[..., RunResult] = run,
    which: Callable[[str], str | None] = shutil.which,
) -> RunResult | None:
    """Install pytest into `python` after asking. Returns None when the user said no."""
    if not confirm(f"Install pytest into {python}? (it downloads a small package from PyPI)"):
        return None
    root = Path(root)
    pip = runner([python, "-m", "pip", "--version"], cwd=root, timeout=30)
    if pip.ok:
        return runner([python, "-m", "pip", "install", "pytest"], cwd=root, timeout=600)
    uv = which("uv")
    if uv:
        return runner([uv, "pip", "install", "--python", python, "pytest"], cwd=root, timeout=600)
    return RunResult(
        args=[python],
        error="This Python has no pip and uv was not found. Install pytest yourself "
        f"(`{python} -m ensurepip` then `{python} -m pip install pytest`).",
    )


# --------------------------------------------------------------------------- first run


def qed_in_gitignore(root: Path) -> bool:
    path = root / ".gitignore"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    return any(line.strip() in (".qed", ".qed/", "/.qed", "/.qed/") for line in lines)


def add_qed_to_gitignore(root: Path | str) -> None:
    """Append `.qed/` to the project's .gitignore (only after the user agreed)."""
    path = Path(root) / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if qed_in_gitignore(Path(root)):
        return
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    path.write_text(f"{existing}{prefix}.qed/\n", encoding="utf-8")


def init_git_repo(root: Path | str) -> str:
    """`git init` plus a first commit of the current files (only after the user agreed)."""
    root = Path(root)
    if not is_git_repo(root):
        git_out(root, "init", "-q")
    git_out(root, "add", "-A")
    git_out(
        root,
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "Initial commit (created by QEDCode)",
        identity=True,
    )
    return git_out(root, "rev-parse", "HEAD")


def first_run(root: Path | str, *, force_detect: bool = False, **detect_kwargs) -> FirstRun:
    """Look at a folder before the first task: git state, saved project config, detection."""
    root = Path(root)
    repo = is_git_repo(root)
    commits = repo and has_commits(root)
    saved = load_project(root)
    confirmed = saved.confirmed and project_config_path(root).exists()
    detection = None
    config = saved
    if force_detect or not confirmed:
        detection = detect_project(root, **detect_kwargs)
        config = detection.config
    problems: list[str] = []
    if not repo:
        problems.append("This folder is not a git repository. QEDCode needs git to work safely.")
    elif not commits:
        problems.append("This repository has no commits yet; QEDCode will make a first commit.")
    if detection and detection.language == "python" and not detection.pytest_ok:
        problems.append("pytest is not available in the chosen Python.")
    if detection and detection.language == "javascript" and not detection.runner_ok:
        problems.append("The JavaScript test runner is not available (see the notes).")
    return FirstRun(
        root=root,
        is_git_repo=repo,
        has_commits=commits,
        already_confirmed=confirmed,
        config=config,
        detection=detection,
        qed_in_gitignore=qed_in_gitignore(root),
        problems=problems,
    )


def confirm_project(
    first: FirstRun,
    *,
    python: str | None = None,
    test_command: list[str] | None = None,
    protected: list[str] | None = None,
) -> ProjectConfig:
    """Save the user's confirmed answers to .qed/project.toml and return the config."""
    config = first.config.model_copy(deep=True)
    if python is not None:
        config.python = python
    if test_command is not None:
        config.test_command = test_command
    if protected is not None:
        config.protected = protected
    config.confirmed = True
    save_project(first.root, config)
    return config


def expand_test_command(config: ProjectConfig, python: str | None = None) -> list[str]:
    """The pytest command with {python} replaced by the project's interpreter."""
    interpreter = python or config.python or sys.executable
    return [interpreter if part == "{python}" else part for part in config.test_command]
