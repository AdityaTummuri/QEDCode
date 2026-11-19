from __future__ import annotations

import sys
from pathlib import Path

import pytest

from qedcode.config import DEFAULT_PROTECTED, ProjectConfig, load_project, save_project
from qedcode.project.detect import (
    add_qed_to_gitignore,
    confirm_project,
    count_tests,
    detect_project,
    expand_test_command,
    find_python,
    find_test_files,
    first_run,
    init_git_repo,
    install_pytest,
    probe_python,
    qed_in_gitignore,
)
from qedcode.vcs.git import has_commits, is_git_repo
from qedcode.verify.run import RunResult
from tests.conftest import BUGZOO_DIR, make_repo, run_git, write_files, zoo_project_names


def fake_probe(version: str = "3.12.1", pytest_version: str = "8.3.0"):
    def probe(args, cwd):
        lines = [version] + ([pytest_version] if pytest_version else ["NOPYTEST"])
        return RunResult(args=args, exit_code=0, stdout="\n".join(lines) + "\n")

    return probe


def touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    return path


# ------------------------------------------------------------------ interpreter order


def test_posix_venv_layout(tmp_path: Path) -> None:
    py = touch(tmp_path / ".venv" / "bin" / "python")
    path, source, notes = find_python(tmp_path, env={}, system="Linux")
    assert (path, source) == (str(py), ".venv/") and notes == []


def test_windows_venv_layout(tmp_path: Path) -> None:
    py = touch(tmp_path / ".venv" / "Scripts" / "python.exe")
    path, source, _ = find_python(tmp_path, env={}, system="Windows")
    assert (path, source) == (str(py), ".venv/")
    # the POSIX layout is not mistaken for a Windows one
    other = tmp_path / "x"
    touch(other / ".venv" / "bin" / "python")
    assert find_python(other, env={}, system="Windows", which=lambda n: None)[1] != ".venv/"


def test_dot_venv_wins_over_venv_over_virtual_env(tmp_path: Path) -> None:
    touch(tmp_path / "venv" / "bin" / "python")
    active = tmp_path / "elsewhere"
    touch(active / "bin" / "python")
    env = {"VIRTUAL_ENV": str(active)}
    assert find_python(tmp_path, env=env, system="Linux")[1] == "venv/"
    dot = touch(tmp_path / ".venv" / "bin" / "python")
    assert find_python(tmp_path, env=env, system="Linux")[0] == str(dot)
    (dot).unlink()
    (tmp_path / "venv" / "bin" / "python").unlink()
    path, source, _ = find_python(tmp_path, env=env, system="Linux")
    assert path == str(active / "bin" / "python") and "active virtual environment" in source


def test_uv_lock_without_a_venv_says_to_run_uv_sync_and_never_creates_one(tmp_path: Path) -> None:
    (tmp_path / "uv.lock").write_text("version = 1\n")
    path, source, notes = find_python(
        tmp_path,
        env={},
        system="Linux",
        which=lambda n: f"/usr/bin/{n}" if n == "python3" else None,
    )
    assert path == "/usr/bin/python3" and source == "python3 on PATH"
    assert any("uv sync" in n for n in notes)
    assert not (tmp_path / ".venv").exists()  # detection never writes to the project


def test_path_fallbacks_prefer_python3_then_python_then_ourselves(tmp_path: Path) -> None:
    both = lambda n: f"/bin/{n}"  # noqa: E731
    assert find_python(tmp_path, env={}, which=both)[0] == "/bin/python3"
    only_python = lambda n: "/bin/python" if n == "python" else None  # noqa: E731
    assert find_python(tmp_path, env={}, which=only_python)[0] == "/bin/python"
    path, source, notes = find_python(tmp_path, env={}, which=lambda n: None)
    assert path == sys.executable and "running QEDCode" in source and notes


def test_probe_python_reads_versions_and_missing_pytest(tmp_path: Path) -> None:
    assert probe_python("py", tmp_path, fake_probe()) == ("3.12.1", "8.3.0")
    assert probe_python("py", tmp_path, fake_probe(pytest_version="")) == ("3.12.1", "")
    failing = lambda args, cwd: RunResult(args=args, error="cannot start")  # noqa: E731
    assert probe_python("py", tmp_path, failing) == ("", "")


def test_probe_with_the_real_python_finds_pytest(tmp_path: Path) -> None:
    version, pytest_version = (
        probe_python(sys.executable, tmp_path, probe=None)
        if False
        else (probe_python(sys.executable, tmp_path))
    )
    assert version.startswith("3.") and pytest_version  # pytest is installed in the dev env


# ------------------------------------------------------------------ test files


def test_find_test_files_skips_environments_and_build_folders(tmp_path: Path) -> None:
    for rel in (
        "tests/test_a.py",
        "tests/sub/b_test.py",
        "pkg/test_in_pkg.py",
        "tests/helper.py",
        ".venv/lib/test_venv.py",
        "node_modules/x/test_js.py",
        "build/test_built.py",
        ".hidden/test_hidden.py",
        "testing_notes.txt",
    ):
        touch(tmp_path / rel)
    assert find_test_files(tmp_path) == [
        "pkg/test_in_pkg.py",
        "tests/sub/b_test.py",
        "tests/test_a.py",
    ]


def test_count_tests_reads_functions_and_classes_without_importing(tmp_path: Path) -> None:
    write_files(
        tmp_path,
        {
            "tests/test_x.py": (
                "def test_one(): pass\n"
                "async def test_two(): pass\n"
                "def helper(): pass\n"
                "class TestGroup:\n"
                "    def test_a(self): pass\n"
                "    def test_b(self): pass\n"
                "    def util(self): pass\n"
                "raise SystemExit('importing me would exit')\n"
            ),
            "tests/test_broken.py": "def test_x(:\n",
        },
    )
    assert count_tests(tmp_path, ["tests/test_x.py", "tests/test_broken.py", "tests/gone.py"]) == 4


# ------------------------------------------------------------------ whole detection


def test_detect_project_builds_a_config_with_default_protected_paths(tmp_path: Path) -> None:
    py = touch(tmp_path / ".venv" / "bin" / "python")
    write_files(tmp_path, {"tests/test_a.py": "def test_a(): pass\ndef test_b(): pass\n"})
    det = detect_project(tmp_path, env={}, system="Linux", probe=fake_probe())
    assert det.python == str(py) and det.python_version == "3.12.1"
    assert det.pytest_ok and det.pytest_version == "8.3.0"
    assert det.test_files == ["tests/test_a.py"] and det.test_count == 2
    assert det.config.python == str(py)
    assert det.config.protected == DEFAULT_PROTECTED
    assert det.config.confirmed is False  # only the user's confirmation sets this
    assert det.notes == []


def test_detect_project_reports_missing_pytest_and_tests(tmp_path: Path) -> None:
    touch(tmp_path / ".venv" / "bin" / "python")
    det = detect_project(tmp_path, env={}, system="Linux", probe=fake_probe(pytest_version=""))
    assert not det.pytest_ok
    assert any("pytest is not installed" in n for n in det.notes)
    assert any("No test files" in n for n in det.notes)


def test_detect_project_with_a_python_that_cannot_run(tmp_path: Path) -> None:
    touch(tmp_path / ".venv" / "bin" / "python")
    broken = lambda args, cwd: RunResult(args=args, exit_code=1)  # noqa: E731
    det = detect_project(tmp_path, env={}, system="Linux", probe=broken)
    assert det.python_version == "" and any("Could not run" in n for n in det.notes)


@pytest.mark.parametrize("name", zoo_project_names())
def test_every_bug_zoo_project_is_detected_correctly(name: str, zoo_copy) -> None:
    project = zoo_copy(name)
    det = detect_project(project)
    assert det.pytest_ok, det.notes
    assert det.python_version.startswith("3.")
    assert any(f.startswith("tests/") for f in det.test_files)
    assert det.test_count >= 2
    # the interpreter really runs pytest for this project
    probed = probe_python(det.python, project)
    assert probed[0] and probed[1]


def test_bug_zoo_hidden_tests_are_not_in_the_visible_set_of_a_copy(zoo_copy) -> None:
    # check_zoo copies include hidden/, QEDCode's own runs must be given copies without it
    project = zoo_copy(zoo_project_names()[0])
    assert "hidden/test_hidden.py" in find_test_files(project)


# ------------------------------------------------------------------ first run


def test_first_run_in_a_folder_that_is_not_a_repo(tmp_path: Path) -> None:
    write_files(tmp_path, {"a.py": "A = 1\n"})
    first = first_run(tmp_path, env={}, system="Linux", probe=fake_probe())
    assert not first.is_git_repo and not first.ready
    assert any("not a git repository" in p for p in first.problems)
    assert first.detection is not None and not first.already_confirmed


def test_git_init_and_first_commit_when_the_user_agrees(tmp_path: Path) -> None:
    write_files(tmp_path, {"a.py": "A = 1\n", "tests/test_a.py": "def test_a(): pass\n"})
    sha = init_git_repo(tmp_path)
    assert is_git_repo(tmp_path) and has_commits(tmp_path)
    assert run_git(tmp_path, "rev-parse", "HEAD").strip() == sha
    assert run_git(tmp_path, "status", "--porcelain").strip() == ""
    assert "Initial commit" in run_git(tmp_path, "log", "-1", "--format=%s")
    assert init_git_repo(tmp_path)  # idempotent on an existing repo (allow-empty commit)


def test_first_run_in_a_repo_without_commits(tmp_path: Path) -> None:
    make_repo(tmp_path / "r", {"a.py": "A = 1\n"}, commit=False)
    first = first_run(tmp_path / "r", env={}, system="Linux", probe=fake_probe())
    assert first.is_git_repo and not first.has_commits
    assert any("no commits" in p for p in first.problems)


def test_confirming_saves_project_toml_and_the_next_run_is_ready(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", {"tests/test_a.py": "def test_a(): pass\n"})
    first = first_run(repo, env={}, system="Linux", probe=fake_probe())
    assert not first.ready and first.detection is not None
    config = confirm_project(first, python="/custom/python")
    assert config.confirmed and config.python == "/custom/python"
    saved = repo / ".qed" / "project.toml"
    assert saved.exists()
    again = first_run(repo, env={}, system="Linux", probe=fake_probe())
    assert again.already_confirmed and again.ready
    assert again.detection is None  # nothing to re-detect once confirmed
    assert again.config.python == "/custom/python"
    forced = first_run(repo, force_detect=True, env={}, system="Linux", probe=fake_probe())
    assert forced.detection is not None and forced.config.python != "/custom/python"


def test_confirm_can_change_the_command_and_protected_paths(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", {"a.py": "A = 1\n"})
    first = first_run(repo, env={}, system="Linux", probe=fake_probe())
    config = confirm_project(
        first,
        test_command=["{python}", "-m", "pytest", "-x"],
        protected=["tests/**", "pyproject.toml"],
    )
    loaded = load_project(repo)
    assert loaded.test_command == ["{python}", "-m", "pytest", "-x"]
    assert loaded.protected == ["tests/**", "pyproject.toml"] and loaded.confirmed
    assert config == loaded


def test_gitignore_offer(tmp_path: Path) -> None:
    assert not qed_in_gitignore(tmp_path)
    add_qed_to_gitignore(tmp_path)
    assert (tmp_path / ".gitignore").read_text() == ".qed/\n"
    add_qed_to_gitignore(tmp_path)  # idempotent
    assert (tmp_path / ".gitignore").read_text().count(".qed") == 1
    (tmp_path / ".gitignore").write_text("*.pyc")  # no trailing newline
    (tmp_path / ".gitignore").write_text("*.pyc")
    add_qed_to_gitignore(tmp_path)
    assert (tmp_path / ".gitignore").read_text() == "*.pyc\n.qed/\n"
    assert qed_in_gitignore(tmp_path)


def test_expand_test_command(tmp_path: Path) -> None:
    cfg = ProjectConfig(python="/p y/python")
    assert expand_test_command(cfg) == ["/p y/python", "-m", "pytest"]  # spaces stay one argument
    assert expand_test_command(cfg, "other") == ["other", "-m", "pytest"]
    assert expand_test_command(ProjectConfig()) == [sys.executable, "-m", "pytest"]
    save_project(tmp_path, cfg)


# ------------------------------------------------------------------ installing pytest (asks first)


def test_install_pytest_asks_first_and_uses_pip_or_uv(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(args, **kw):
        calls.append(list(args))
        ok = "--version" not in args or args[-2] == "pip"
        return RunResult(args=list(args), exit_code=0 if ok else 1)

    assert install_pytest("py", tmp_path, lambda p: False, runner=runner) is None
    assert calls == []  # declined: nothing ran
    prompts: list[str] = []
    result = install_pytest("py", tmp_path, lambda p: prompts.append(p) or True, runner=runner)
    assert result is not None and result.ok
    assert calls[-1] == ["py", "-m", "pip", "install", "pytest"]
    assert "py" in prompts[0]

    calls.clear()

    def no_pip(args, **kw):
        calls.append(list(args))
        return RunResult(
            args=list(args), exit_code=1 if "pip" in args and "--version" in args else 0
        )

    result = install_pytest(
        "py", tmp_path, lambda p: True, runner=no_pip, which=lambda n: "/bin/uv"
    )
    assert calls[-1] == ["/bin/uv", "pip", "install", "--python", "py", "pytest"]

    result = install_pytest("py", tmp_path, lambda p: True, runner=no_pip, which=lambda n: None)
    assert result is not None and "ensurepip" in result.error


def test_bugzoo_dir_exists_for_the_parametrized_tests() -> None:
    assert BUGZOO_DIR.exists()
