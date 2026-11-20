"""Tests appended to an existing test file (`qed check`), and weakened tests capping the grade."""

from __future__ import annotations

from pathlib import Path

import pytest

from qedcode.config import ProjectConfig
from qedcode.models.schemas import Baseline, Candidate, CheckResult, Grade, Speed
from qedcode.verify.grade import grade
from qedcode.verify.guard import (
    PROTECTED_MARKER,
    WEAKENED_MARKER,
    parse_diff,
    protected_from_checks,
    scope_check,
    weakened_from_checks,
)
from qedcode.verify.test_additions import added_test_names, appended_tests, is_new_test_key
from tests.unit.test_guard_lint import make_diff

PROJECT = ProjectConfig(python="python")
OLD = """from shop.pages import paginate


def test_empty():
    assert paginate([], 1, 10) == []


class TestPages:
    def test_past_the_end(self):
        assert paginate([1], 5, 2) == []
"""


# ------------------------------------------------------------------ what counts as only adding


def test_appending_test_functions_and_classes_is_a_pure_addition() -> None:
    new = OLD + (
        "\n\nimport pytest\n\n\n"
        "@pytest.mark.parametrize('n', [1, 2])\n"
        "def test_full_page(n):\n    assert len(paginate([1, 2, 3, 4], 1, n)) == n\n\n\n"
        "class TestMore:\n    def test_last(self):\n        assert paginate([1, 2, 3], 2, 2) == [3]\n"
    )
    assert added_test_names(OLD, new) == {"test_full_page", "TestMore"}


def test_a_test_inserted_between_old_ones_counts_too() -> None:
    new = OLD.replace(
        "\n\nclass TestPages", "\n\ndef test_new():\n    assert 1 == 1\n\n\nclass TestPages"
    )
    assert added_test_names(OLD, new) == {"test_new"}


@pytest.mark.parametrize(
    "new",
    [
        OLD.replace("== []\n\n\nclass", "== [0]\n\n\nclass"),  # an old assert changed
        OLD.replace("def test_empty():\n    assert paginate([], 1, 10) == []\n", ""),  # removed
        OLD + "\n\ndef test_empty():\n    assert True\n",  # shadows an old test
        OLD + "\n\ndef paginate(*a):\n    return []\n",  # shadows an import, not a test
        OLD + "\n\nfrom fake import paginate\n",  # an import re-binding an old name
        OLD + "\n\nimport shop.pages\nshop.pages.paginate = lambda *a: []\n",  # module code
        OLD + "\n\n@pytest.fixture(autouse=True)\ndef test_patch():\n    pass\n",  # autouse fixture
        OLD + "\n\n@pytest.mark.skip\ndef test_new():\n    assert 1 == 2\n",  # skip decorator
        "class TestPages:\n    pass\n\n\n" + OLD.split("\n\n\nclass")[0],  # reordered
        OLD + "\n\ndef test_bad(:\n",  # does not parse
        OLD,  # nothing added at all
    ],
)
def test_anything_more_than_adding_tests_is_not_a_pure_addition(new: str) -> None:
    assert added_test_names(OLD, new) is None


def test_only_modified_python_test_files_are_considered(tmp_path: Path) -> None:
    new = OLD + "\n\ndef test_new():\n    assert paginate([1, 2], 1, 2) == [1, 2]\n"
    diff = make_diff(
        tmp_path,
        {"tests/test_pages.py": OLD, "src/pages.py": "A = 1\n", "tests/conftest.py": "X = 1\n"},
        {"tests/test_pages.py": new, "src/pages.py": "A = 2\n", "tests/conftest.py": OLD},
    )
    files = parse_diff(diff)
    before = {"tests/test_pages.py": OLD, "src/pages.py": "A = 1\n", "tests/conftest.py": "X = 1\n"}
    after = {"tests/test_pages.py": new, "src/pages.py": "A = 2\n", "tests/conftest.py": OLD}
    found = appended_tests(files, before.__getitem__, after.__getitem__)
    assert found == {"tests/test_pages.py": {"test_new"}}


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("tests.test_pages::test_new", True),
        ("tests.test_pages::test_new[2]", True),
        ("tests.test_pages.TestMore::test_last", True),
        ("tests.test_pages::test_empty", False),
        ("tests.test_pages.TestPages::test_past_the_end", False),
    ],
)
def test_junit_keys_are_matched_to_the_added_tests(key: str, expected: bool) -> None:
    assert is_new_test_key(key, {"test_new", "TestMore"}) is expected


# ------------------------------------------------------------------ the scope stage


def test_an_appended_test_file_is_not_a_protected_change(tmp_path: Path) -> None:
    new = OLD + "\n\ndef test_new():\n    assert paginate([1, 2], 1, 2) == [1, 2]\n"
    diff = make_diff(tmp_path, {"tests/test_pages.py": OLD}, {"tests/test_pages.py": new})
    plain = scope_check(diff, PROJECT, "user", Speed.NORMAL)
    assert protected_from_checks([plain]) == ["tests/test_pages.py"]
    appended = scope_check(
        diff, PROJECT, "user", Speed.NORMAL, appended={"tests/test_pages.py": {"test_new"}}
    )
    assert protected_from_checks([appended]) == [] and PROTECTED_MARKER not in appended.details


@pytest.mark.parametrize(
    "line",
    [
        "assert True",
        "assert True or paginate([1], 1, 1) == [1]",
        "assert paginate([1], 1, 1) == [9] or True",
        "assert (True)",
        "assert 1",
        "assert 1, 'always'",
    ],
)
def test_an_assert_that_can_never_fail_marks_the_test_weakened(tmp_path: Path, line: str) -> None:
    base = {"tests/test_a.py": "def test_a():\n    assert f() == 1\n"}
    diff = make_diff(tmp_path, base, {"tests/test_a.py": f"def test_a():\n    {line}\n"})
    check = scope_check(diff, PROJECT, "user", Speed.NORMAL)
    assert check.passed  # qed check reports, never rejects
    assert weakened_from_checks([check]) == ["tests/test_a.py"]
    assert "can never fail" in check.details


@pytest.mark.parametrize(
    "line", ["assert 10 == f()", "assert True is f()", "assert f() or g()", "assert 1 == f()"]
)
def test_ordinary_asserts_are_not_mistaken_for_always_true(tmp_path: Path, line: str) -> None:
    base = {"tests/test_a.py": "def test_a():\n    assert f() == 1\n"}
    diff = make_diff(tmp_path, base, {"tests/test_a.py": f"def test_a():\n    {line}\n"})
    assert weakened_from_checks([scope_check(diff, PROJECT, "user", Speed.NORMAL)]) == []


@pytest.mark.parametrize(
    "line",
    [
        "expect(true).toBe(true);",
        "expect(1).toBeTruthy();",
        "assert(true);",
        "assert.ok(true, 'x');",
        "t.true(true);",
    ],
)
def test_js_asserts_that_can_never_fail_are_caught(tmp_path: Path, line: str) -> None:
    base = {"test/a.test.js": "test('a', () => {\n  expect(f()).toBe(1);\n});\n"}
    changed = {"test/a.test.js": f"test('a', () => {{\n  {line}\n}});\n"}
    diff = make_diff(tmp_path, base, changed)
    assert weakened_from_checks([scope_check(diff, PROJECT, "user", Speed.NORMAL)])


def test_skips_and_removed_asserts_mark_a_user_change_weakened(tmp_path: Path) -> None:
    base = {"tests/test_a.py": "def test_a():\n    assert f() == 1\n    assert g() == 2\n"}
    removed = make_diff(tmp_path / "r", base, {"tests/test_a.py": "def test_a():\n    pass\n"})
    skipped = make_diff(
        tmp_path / "s",
        base,
        {"tests/test_a.py": "import pytest\n\n@pytest.mark.skip\n" + base["tests/test_a.py"]},
    )
    for diff in (removed, skipped):
        check = scope_check(diff, PROJECT, "user", Speed.NORMAL)
        assert check.passed and f"{WEAKENED_MARKER} tests/test_a.py" in check.details


def test_generated_candidates_are_judged_as_before(tmp_path: Path) -> None:
    """`assert 1` in the test writer's own new test is not flagged: that rule is for qed check."""
    diff = make_diff(
        tmp_path, {"src/a.py": "A = 1\n"}, {"tests/test_new.py": "def test_n():\n    assert 1\n"}
    )
    check = scope_check(
        diff, PROJECT, "test_writer", Speed.NORMAL, allowed_added=["tests/test_new.py"]
    )
    assert check.passed and WEAKENED_MARKER not in check.details


# ------------------------------------------------------------------ the grade


def _cand(*checks: CheckResult, tests: int = 5, full: bool = True) -> Candidate:
    return Candidate(
        id="u1", role="user", checks=list(checks), tests_passed=tests, full_suite_ran=full
    )


def test_weakened_tests_cap_the_grade_at_c() -> None:
    base = Baseline(commit="abc")
    scope = CheckResult(stage="scope", passed=True, details=f"{WEAKENED_MARKER} tests/test_a.py")
    passing = [CheckResult(stage=s, passed=True) for s in ("parse", "fbpa", "targeted", "full")]
    assert grade(_cand(*passing), base) is Grade.A
    assert grade(_cand(*passing, scope), base) is Grade.C


@pytest.mark.parametrize(
    ("key", "path", "expected"),
    [
        ("tests.test_pages::test_new", "tests/test_pages.py", True),
        ("test_pages::test_new", "tests/test_pages.py", True),
        ("tests.test_pages.TestMore::test_last", "tests/test_pages.py", True),
        ("tests.test_other::test_new", "tests/test_pages.py", False),
        ("tests.test_pages_extra::test_new", "tests/test_pages.py", False),
    ],
)
def test_junit_keys_are_matched_to_their_file(key: str, path: str, expected: bool) -> None:
    from qedcode.verify.test_additions import key_in_file

    assert key_in_file(key, path) is expected
