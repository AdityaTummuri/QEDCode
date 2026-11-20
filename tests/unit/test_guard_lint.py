from __future__ import annotations

from pathlib import Path

import pytest

from qedcode.config import DEFAULT_PROTECTED, ProjectConfig
from qedcode.engine import budget
from qedcode.models.schemas import Grade, Speed, TaskKind
from qedcode.vcs import worktree
from qedcode.vcs.snapshot import snapshot
from qedcode.verify import lint
from qedcode.verify.guard import (
    RISKY_PATTERNS,
    changed_paths,
    count_changed_lines,
    parse_diff,
    protected_changes_in,
    risk_check,
    risk_findings,
    scope_check,
)
from qedcode.verify.lint import lint_check, parse_check
from tests.conftest import make_repo, write_files

PROJECT = ProjectConfig(python="python")


def make_diff(
    tmp_path: Path, base: dict[str, str | bytes], changes: dict[str, str | bytes], delete=()
) -> str:
    """A real `git diff` between `base` files and the same tree with `changes` applied."""
    repo = make_repo(tmp_path / "repo", base)
    snap = snapshot(repo, "t-d")
    wt = worktree.create(repo, snap, "cand")
    write_files(wt, changes)
    for rel in delete:
        (wt / rel).unlink()
    commit = worktree.commit_all(wt, "c")
    diff = worktree.diff(repo, snap, commit)
    worktree.remove(repo, wt)
    return diff


def added(n: int, prefix: str = "x") -> str:
    return "".join(f"{prefix}{i} = {i}\n" for i in range(n))


# ------------------------------------------------------------------ diff parsing


def test_parse_diff_on_a_real_diff(tmp_path: Path) -> None:
    diff = make_diff(
        tmp_path,
        {
            "a.py": "A = 1\nB = 2\n",
            "gone.py": "G = 1\n",
            "my dir/with space.py": "S = 1\n",
            "data.bin": bytes(range(256)),
        },
        {
            "a.py": "A = 10\nB = 2\nC = 3\n",
            "new.py": "N = 1\n",
            "my dir/with space.py": "S = 2\n",
            "data.bin": bytes(reversed(range(256))),
        },
        delete=["gone.py"],
    )
    files = {f.path: f for f in parse_diff(diff)}
    assert set(files) == {"a.py", "gone.py", "my dir/with space.py", "new.py", "data.bin"}
    assert files["a.py"].status == "modified"
    assert [t for _, t in files["a.py"].added] == ["A = 10", "C = 3"] and files["a.py"].removed == [
        "A = 1"
    ]
    assert [n for n, _ in files["a.py"].added] == [1, 3]  # new-file line numbers
    assert files["gone.py"].status == "deleted" and files["gone.py"].removed == ["G = 1"]
    assert files["new.py"].status == "added" and files["new.py"].added == [(1, "N = 1")]
    assert files["data.bin"].binary and files["data.bin"].changed_lines == 0
    assert files["my dir/with space.py"].added == [(1, "S = 2")]
    assert count_changed_lines(diff) == 2 + 1 + 1 + 1 + 1 + 1
    assert sorted(changed_paths(diff)) == sorted(files)


def test_parse_diff_of_nothing() -> None:
    assert parse_diff("") == [] and count_changed_lines("") == 0


# ------------------------------------------------------------------ scope


PROTECTED_SAMPLES = [
    "tests/test_pages.py",
    "tests/helpers/util.py",
    "test/test_x.py",
    "pkg/test_inline.py",
    "pkg/inline_test.py",
    "conftest.py",
    "pkg/conftest.py",
    "pytest.ini",
    "tox.ini",
    "setup.cfg",
    "pyproject.toml",
    "noxfile.py",
    ".github/workflows/ci.yml",
]


@pytest.mark.parametrize("path", PROTECTED_SAMPLES)
def test_every_protected_pattern_is_rejected_at_scope(tmp_path: Path, path: str) -> None:
    diff = make_diff(tmp_path, {path: "A = 1\n"}, {path: "A = 2\n"})
    check = scope_check(diff, PROJECT, "coder", Speed.NORMAL)
    assert check.stage == "scope" and not check.passed
    assert f"{path} is a protected file" in check.summary
    assert "Change the code under test instead" in check.details
    assert protected_changes_in(diff, PROJECT) == [path]


def test_default_protected_patterns_are_all_covered_by_the_samples() -> None:
    assert len(PROTECTED_SAMPLES) >= len(DEFAULT_PROTECTED)


def test_editing_tests_test_pages_is_rejected_with_a_clear_message(tmp_path: Path) -> None:
    diff = make_diff(
        tmp_path,
        {"tests/test_pages.py": "def test_a():\n    assert True\n"},
        {"tests/test_pages.py": "def test_a():\n    pass\n"},
    )
    check = scope_check(diff, PROJECT, "coder", Speed.NORMAL)
    assert not check.passed and check.summary.startswith(
        "Rejected: tests/test_pages.py is a protected file"
    )


def test_source_files_pass_scope(tmp_path: Path) -> None:
    diff = make_diff(
        tmp_path, {"src/a.py": "A = 1\n"}, {"src/a.py": "A = 2\n", "src/b.py": "B = 1\n"}
    )
    check = scope_check(diff, PROJECT, "coder", Speed.NORMAL)
    assert check.passed and "2 file(s)" in check.summary and "within scope" in check.summary


def test_protected_edits_can_be_allowed_by_the_task(tmp_path: Path) -> None:
    diff = make_diff(
        tmp_path,
        {"tests/test_a.py": "def test_a():\n    assert 1\n"},
        {"tests/test_a.py": "def test_a():\n    assert 2\n"},
    )
    assert scope_check(diff, PROJECT, "coder", Speed.NORMAL, allow_protected=True).passed


def test_deleted_files_are_rejected(tmp_path: Path) -> None:
    diff = make_diff(
        tmp_path, {"src/a.py": "A = 1\n", "src/b.py": "B = 1\n"}, {}, delete=["src/b.py"]
    )
    check = scope_check(diff, PROJECT, "coder", Speed.NORMAL)
    assert not check.passed and "src/b.py was deleted" in check.details


def test_a_coder_creating_a_test_file_is_rejected_but_the_repro_test_is_allowed(
    tmp_path: Path,
) -> None:
    diff = make_diff(
        tmp_path, {"src/a.py": "A = 1\n"}, {"tests/test_new.py": "def test_n():\n    assert 1\n"}
    )
    rejected = scope_check(diff, PROJECT, "coder", Speed.NORMAL)
    assert not rejected.passed and "new test file" in rejected.details
    allowed = scope_check(diff, PROJECT, "coder", Speed.NORMAL, allowed_added=["tests/test_new.py"])
    assert allowed.passed
    # the same file under a non-protected name is still a test file
    diff2 = make_diff(
        tmp_path / "two",
        {"src/a.py": "A = 1\n"},
        {"src/test_extra.py": "def test_n():\n    assert 1\n"},
    )
    assert (
        "new test file"
        in scope_check(diff2, ProjectConfig(protected=[]), "coder", Speed.NORMAL).details
    )


def test_the_allowed_repro_test_must_not_skip_itself(tmp_path: Path) -> None:
    diff = make_diff(
        tmp_path,
        {"src/a.py": "A = 1\n"},
        {"tests/test_new.py": "import pytest\n@pytest.mark.skip\ndef test_n():\n    assert 1\n"},
    )
    check = scope_check(diff, PROJECT, "coder", Speed.NORMAL, allowed_added=["tests/test_new.py"])
    assert not check.passed and "adds a skip or xfail" in check.details


@pytest.mark.parametrize(
    "line",
    [
        "@pytest.mark.skip",
        "@pytest.mark.skipif(True, reason='x')",
        "pytest.skip('later')",
        "@pytest.mark.xfail",
        "pytest.xfail('known')",
        "@unittest.skip('x')",
        "self.skipTest('x')",
    ],
)
def test_adding_skip_or_xfail_to_a_test_is_caught_even_when_test_edits_are_allowed(
    tmp_path: Path, line: str
) -> None:
    base = {"tests/test_a.py": "import pytest\n\ndef test_a():\n    assert 1\n"}
    changed = {"tests/test_a.py": f"import pytest\n\n{line}\ndef test_a():\n    assert 1\n"}
    diff = make_diff(tmp_path, base, changed)
    check = scope_check(diff, PROJECT, "coder", Speed.NORMAL, allow_protected=True)
    assert not check.passed and "adds a skip or xfail" in check.details


def test_removing_asserts_from_a_test_is_caught(tmp_path: Path) -> None:
    base = {"tests/test_a.py": "def test_a():\n    assert f() == 1\n    assert g() == 2\n"}
    diff = make_diff(tmp_path, base, {"tests/test_a.py": "def test_a():\n    assert f() == 1\n"})
    check = scope_check(diff, PROJECT, "coder", Speed.NORMAL, allow_protected=True)
    assert not check.passed and "removes 1 assert line(s)" in check.details


def test_changing_an_assert_is_not_removing_it(tmp_path: Path) -> None:
    base = {"tests/test_a.py": "def test_a():\n    assert f() == 1\n"}
    diff = make_diff(tmp_path, base, {"tests/test_a.py": "def test_a():\n    assert f() == 2\n"})
    assert scope_check(diff, PROJECT, "coder", Speed.NORMAL, allow_protected=True).passed


@pytest.mark.parametrize(
    ("speed", "limit"), [(Speed.FAST, 200), (Speed.NORMAL, 300), (Speed.SURE, 600)]
)
def test_the_size_limit_follows_the_speed(tmp_path: Path, speed: Speed, limit: int) -> None:
    base = {"src/a.py": "A = 1\n"}
    diff = make_diff(tmp_path, base, {"src/b.py": added(limit)})
    assert scope_check(diff, PROJECT, "coder", speed).passed
    diff_over = make_diff(tmp_path / "over", base, {"src/b.py": added(limit + 1)})
    check = scope_check(diff_over, PROJECT, "coder", speed)
    assert not check.passed and f"limit at this speed is {limit}" in check.details


def test_size_limit_counts_removed_lines_too(tmp_path: Path) -> None:
    base = {"src/a.py": added(250)}
    diff = make_diff(tmp_path, base, {"src/a.py": added(250, prefix="y")})  # 250 out + 250 in
    assert not scope_check(diff, PROJECT, "coder", Speed.NORMAL).passed
    assert scope_check(diff, PROJECT, "coder", Speed.SURE).passed


def test_user_role_only_reports(tmp_path: Path) -> None:
    diff = make_diff(
        tmp_path,
        {"tests/test_a.py": "def test_a():\n    assert 1\n"},
        {"tests/test_a.py": "def test_a():\n    pass\n"},
    )
    check = scope_check(diff, PROJECT, "user", Speed.FAST)
    assert check.passed and "not blocking" in check.summary and "protected file" in check.details
    clean = scope_check(
        make_diff(tmp_path / "c", {"a.py": "A = 1\n"}, {"a.py": "A = 2\n"}),
        PROJECT,
        "user",
        Speed.FAST,
    )
    assert clean.passed and "nothing unusual" in clean.summary


def test_test_writer_may_add_test_files(tmp_path: Path) -> None:
    diff = make_diff(
        tmp_path, {"src/a.py": "A = 1\n"}, {"tests/test_new.py": "def test_n():\n    assert 1\n"}
    )
    assert scope_check(
        diff, PROJECT, "test_writer", Speed.NORMAL, allowed_added=["tests/test_new.py"]
    ).passed


# ------------------------------------------------------------------ risk


RISKY_LINES = {
    "os.system(": "os.system('ls')",
    "subprocess": "import subprocess",
    "shutil.rmtree(": "shutil.rmtree(path)",
    "os.remove(": "os.remove(p)",
    "os.unlink(": "os.unlink(p)",
    "os.rmdir(": "os.rmdir(p)",
    "socket": "import socket",
    "requests.": "r = requests.get(url)",
    "urllib": "from urllib.request import urlopen",
    "httpx.": "httpx.get(url)",
    "eval(": "value = eval(text)",
    "exec(": "exec(code)",
    "__import__(": "mod = __import__(name)",
    "open() with an absolute path": "f = open('/etc/passwd')",
    "Path.home(": "base = Path.home() / 'x'",
}


def test_every_risky_pattern_has_a_case() -> None:
    assert {name for name, _ in RISKY_PATTERNS} == set(RISKY_LINES)


@pytest.mark.parametrize(("name", "line"), list(RISKY_LINES.items()))
def test_each_risky_pattern_is_flagged_with_its_location(
    tmp_path: Path, name: str, line: str
) -> None:
    diff = make_diff(tmp_path, {"app/run.py": "A = 1\n"}, {"app/run.py": f"A = 1\n{line}\n"})
    check = risk_check(diff)
    assert check.stage == "risk" and not check.passed and check.needs_approval
    assert f"Risky pattern: {name} in app/run.py" == check.summary
    assert "app/run.py:2" in check.details
    findings = risk_findings(diff)
    assert [(f.pattern, f.path, f.line) for f in findings][0] == (name, "app/run.py", 2)


def test_import_subprocess_is_flagged_at_risk(tmp_path: Path) -> None:
    diff = make_diff(tmp_path, {"a.py": "A = 1\n"}, {"a.py": "import subprocess\nA = 1\n"})
    assert risk_check(diff).needs_approval


@pytest.mark.parametrize(
    "line",
    [
        "model.eval()",
        "x = obj.exec(1)",
        "f = open('data.txt')",
        "# subprocess is not used",
        "name = 'socketing'",
        "requests_total = 1",
        "result = evaluate(1)",
    ],
)
def test_safe_lines_are_not_flagged(tmp_path: Path, line: str) -> None:
    diff = make_diff(tmp_path, {"a.py": "A = 1\n"}, {"a.py": f"A = 1\n{line}\n"})
    check = risk_check(diff)
    assert check.passed and not check.needs_approval


def test_only_added_python_lines_are_scanned(tmp_path: Path) -> None:
    base = {"a.py": "import subprocess\nA = 1\n", "notes.md": "uses subprocess\n"}
    removed_only = make_diff(tmp_path, base, {"a.py": "A = 1\n"})
    assert risk_check(removed_only).passed  # the risky line was removed, not added
    md = make_diff(tmp_path / "md", base, {"notes.md": "uses subprocess and os.system(x)\n"})
    assert risk_check(md).passed  # not a Python file
    untouched = make_diff(
        tmp_path / "u",
        {"a.py": "import subprocess\nA = 1\n"},
        {"a.py": "import subprocess\nA = 2\n"},
    )
    assert risk_check(untouched).passed  # the existing import is context, not added


# ------------------------------------------------------------------ parse stage


def test_parse_check(tmp_path: Path) -> None:
    write_files(tmp_path, {"ok.py": "A = 1\n", "bad.py": "def f(:\n", "notes.md": "x"})
    assert parse_check(tmp_path, ["ok.py", "notes.md"]).passed
    bad = parse_check(tmp_path, ["ok.py", "bad.py"])
    assert not bad.passed and bad.stage == "parse"
    assert "bad.py, line 1" in bad.summary and "bad.py, line 1" in bad.details
    assert "No source files changed" in parse_check(tmp_path, ["notes.md"]).summary
    assert parse_check(tmp_path, ["deleted.py"]).passed  # a deleted file has nothing to parse


# ------------------------------------------------------------------ lint stage


def lint_case(tmp_path: Path, base: dict[str, str], changes: dict[str, str]):
    repo = make_repo(tmp_path / "repo", base)
    snap = snapshot(repo, "t-l")
    wt = worktree.create(repo, snap, "cand")
    write_files(wt, changes)
    check = lint_check(wt, repo, snap, list(changes))
    worktree.remove(repo, wt)
    return check


def test_a_new_undefined_name_fails_lint(tmp_path: Path) -> None:
    check = lint_case(
        tmp_path, {"a.py": "def f():\n    return 1\n"}, {"a.py": "def f():\n    return missing\n"}
    )
    assert check.stage == "lint" and not check.passed
    assert "F821" in check.summary and "a.py:2" in check.details


def test_old_errors_are_not_blamed_on_the_candidate(tmp_path: Path) -> None:
    base = {"a.py": "def f():\n    return old_problem\n"}
    same = lint_case(tmp_path, base, {"a.py": "def f():\n    return old_problem  # still there\n"})
    assert same.passed and "No new serious lint errors" in same.summary
    fixed = lint_case(tmp_path / "fixed", base, {"a.py": "def f():\n    return 1\n"})
    assert fixed.passed


def test_a_second_error_of_the_same_kind_counts_as_new(tmp_path: Path) -> None:
    base = {"a.py": "def f():\n    return old_problem\n"}
    more = {"a.py": "def f():\n    return old_problem\n\ndef g():\n    return another_problem\n"}
    assert not lint_case(tmp_path, base, more).passed


def test_a_different_new_error_fails_even_when_old_ones_exist(tmp_path: Path) -> None:
    base = {"a.py": "def f():\n    return old_problem\n"}
    check = lint_case(tmp_path, base, {"a.py": "def f():\n    return old_problem\n\nreturn 5\n"})
    assert not check.passed and "F706" in check.details


def test_a_syntax_error_fails_lint_too(tmp_path: Path) -> None:
    check = lint_case(tmp_path, {"a.py": "A = 1\n"}, {"a.py": "def f(:\n"})
    assert not check.passed and "syntax-error" in check.details or "invalid-syntax" in check.details


def test_a_new_file_is_compared_with_nothing(tmp_path: Path) -> None:
    assert lint_case(tmp_path, {"a.py": "A = 1\n"}, {"new.py": "B = 2\n"}).passed
    assert not lint_case(tmp_path / "bad", {"a.py": "A = 1\n"}, {"new.py": "B = nope\n"}).passed


def test_style_problems_are_not_fatal(tmp_path: Path) -> None:
    messy = "import os,sys\nx=1\nunused = 2\n"
    assert lint_case(tmp_path, {"a.py": "A = 1\n"}, {"a.py": messy}).passed


def test_project_ruff_config_cannot_hide_errors(tmp_path: Path) -> None:
    config = "[tool.ruff.lint]\nignore = ['F821']\n"
    check = lint_case(
        tmp_path, {"pyproject.toml": config, "a.py": "A = 1\n"}, {"a.py": "B = undefined_name\n"}
    )
    assert not check.passed  # --isolated ignores the project's own ruff settings


def test_no_python_files_means_nothing_to_lint(tmp_path: Path) -> None:
    check = lint_case(tmp_path, {"a.py": "A = 1\n"}, {"README.md": "hi\n"})
    assert check.passed and "No Python files" in check.summary


def test_lint_is_skipped_not_failed_when_ruff_cannot_run(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(lint, "_ruff", lambda files: (None, "ruff is missing"))
    check = lint_case(tmp_path, {"a.py": "A = 1\n"}, {"a.py": "A = 2\n"})
    assert check.passed and "skipped" in check.summary and "ruff is missing" in check.details


# ------------------------------------------------------------------ the speed dial


def test_speed_dial_numbers_match_the_brief() -> None:
    fast, normal, sure = (budget.settings_for(s) for s in (Speed.FAST, Speed.NORMAL, Speed.SURE))
    assert (fast.tries, normal.tries, sure.tries) == (1, 3, 6)
    assert fast.temperatures == (0.2,) and normal.temperatures == (0.2, 0.7, 0.9)
    assert sure.temperatures == (0.2, 0.6, 0.7, 0.8, 0.9, 1.0)
    assert (fast.repair_rounds, normal.repair_rounds, sure.repair_rounds) == (0, 1, 2)
    assert (fast.max_files, normal.max_files, sure.max_files) == (3, 5, 8)
    assert (fast.max_changed_lines, normal.max_changed_lines, sure.max_changed_lines) == (
        200,
        300,
        600,
    )
    assert (fast.time_budget_s, normal.time_budget_s, sure.time_budget_s) == (300, 900, 1800)
    assert (fast.baseline_full, normal.baseline_full, sure.baseline_full) == (False, True, True)
    assert (fast.full_on_finalist, normal.full_on_finalist) == (False, True)
    assert (fast.helper, normal.helper, sure.helper) == ("never", "after_repairs", "after_repairs")
    assert budget.settings_for("normal") is budget.settings_for(Speed.NORMAL)


def test_test_writer_rules() -> None:
    assert not any(budget.test_writer_on(Speed.FAST, k) for k in TaskKind)
    on_normal = {k for k in TaskKind if budget.test_writer_on(Speed.NORMAL, k)}
    assert on_normal == {TaskKind.BUGFIX, TaskKind.FEATURE}
    on_sure = {k for k in TaskKind if budget.test_writer_on(Speed.SURE, k)}
    assert on_sure == set(TaskKind) - {TaskKind.DOCS}


def test_target_grade_rules_and_helpers() -> None:
    assert budget.target_grade(Speed.FAST, True) is Grade.B
    assert budget.target_grade(Speed.NORMAL, True) is Grade.A
    assert budget.target_grade(Speed.NORMAL, False) is Grade.B
    assert budget.target_grade(Speed.SURE, False) is Grade.A
    assert budget.temperatures_for(Speed.NORMAL) == [0.2, 0.7, 0.9]
    assert budget.temperatures_for(Speed.FAST) == [0.2]
    assert [budget.next_speed(s) for s in (Speed.FAST, Speed.NORMAL, Speed.SURE)] == [
        Speed.NORMAL,
        Speed.SURE,
        Speed.SURE,
    ]


# ------------------------------------------------------------------ odd file names


def test_quoted_paths_in_a_real_diff_are_still_guarded(tmp_path: Path) -> None:
    """git quotes names with quotes, tabs or backslashes; the guards must still see the file."""
    from qedcode.verify.guard import parse_diff, risk_findings
    from tests.conftest import make_repo, write_files

    repo = make_repo(tmp_path / "r", {"app/ok.py": "X = 1\n"})
    base = snapshot(repo, "t-q1")
    wt = worktree.create(repo, base, "t-q1-c1")
    write_files(wt, {'app/we"ird.py': "import subprocess\n", "app/ok.py": "X = 2\n"})
    commit = worktree.commit_all(wt, "odd name")
    diff = worktree.diff(repo, base, commit)
    worktree.remove(repo, wt)
    assert '"a/app/we\\"ird.py"' in diff  # git really quotes it
    paths = {f.path: f for f in parse_diff(diff)}
    assert set(paths) == {'app/we"ird.py', "app/ok.py"}
    assert paths['app/we"ird.py'].status == "added"
    assert paths["app/ok.py"].added == [(1, "X = 2")]  # lines are not mixed between files
    assert [f.path for f in risk_findings(diff)] == ['app/we"ird.py']


@pytest.mark.parametrize(
    "path", ['app/a"b.py', "app/tab\there.py", "-p.py", "app/--rootdir.py", "app/x\x00.py"]
)
def test_model_paths_with_quotes_control_chars_or_leading_dashes_are_refused(path: str) -> None:
    from qedcode.project.paths import validate_path

    assert validate_path(path) is not None


def test_ordinary_paths_still_pass_validation() -> None:
    from qedcode.project.paths import validate_path

    for path in ("app/my file.py", "src/pkg/mod-x.py", "a/b/c_d.py", "pkg/ünï.py"):
        assert validate_path(path) is None, path
