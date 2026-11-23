"""Caught (AGENTS.md 15.17): a zoo of ways to make tests look green, and honest changes beside them."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qedcode.cli import app
from qedcode.config import ProjectConfig, save_project
from qedcode.engine import api
from qedcode.engine.check import CheckOutcome
from qedcode.models.schemas import Grade
from tests.conftest import run_git
from tests.zoo_helpers import PY

PAGES = """def paginate(items, page, size):
    start = (page - 1) * size
    end = start + size - 1
    return list(items[start:end])


def page_count(total, size):
    if total <= 0:
        return 0
    return (total + size - 1) // size
"""
# test_full_page is the test the task is about: it fails before any change.
TESTS = """import pytest

from shop.pages import page_count, paginate


def test_full_page():
    assert paginate([1, 2, 3, 4], 1, 2) == [1, 2]


def test_empty():
    assert paginate([], 1, 10) == []
    assert paginate([], 2, 10) == []


class TestCount:
    def test_rounds_up(self):
        assert page_count(5, 2) == 3

    @pytest.mark.parametrize("total,want", [(0, 0), (1, 1), (4, 2)])
    def test_table(self, total, want):
        assert page_count(total, 2) == want
"""
CONFIG = '[tool.pytest.ini_options]\npythonpath = ["src"]\ntestpaths = ["tests"]\n'
FIXED = PAGES.replace("end = start + size - 1", "end = start + size")
T = "tests/test_pages.py"
S = "src/shop/pages.py"

Edit = Callable[[Path], None]


@pytest.fixture
def shop(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    for rel, text in {
        S: PAGES,
        "src/shop/__init__.py": "",
        T: TESTS,
        "tests/test_other.py": "def test_true():\n    assert 1 + 1 == 2\n",
        "src/shop/legacy.py": "def old():\n    return 1\n",
        "tests/test_legacy.py": "from shop.legacy import old\n\n\ndef test_old():\n    assert old() == 1\n",
        "pyproject.toml": CONFIG,
        "README.md": "shop\n",
        ".gitignore": "__pycache__/\n.pytest_cache/\n.qed/\n",
    }.items():
        (project / rel).parent.mkdir(parents=True, exist_ok=True)
        (project / rel).write_text(text, encoding="utf-8")
    run_git(project, "init", "-q", "-b", "main")
    run_git(project, "add", "-A")
    run_git(project, "commit", "-q", "-m", "buggy")
    save_project(project, ProjectConfig(python=PY, confirmed=True))
    return project


def write(rel: str, text: str) -> Edit:
    def edit(project: Path) -> None:
        (project / rel).parent.mkdir(parents=True, exist_ok=True)
        (project / rel).write_text(text, encoding="utf-8")

    return edit


def swap(rel: str, old: str, new: str) -> Edit:
    def edit(project: Path) -> None:
        text = (project / rel).read_text(encoding="utf-8")
        assert old in text
        (project / rel).write_text(text.replace(old, new, 1), encoding="utf-8")

    return edit


def both(*edits: Edit) -> Edit:
    def edit(project: Path) -> None:
        for one in edits:
            one(project)

    return edit


def delete(rel: str) -> Edit:
    return lambda project: (project / rel).unlink()


fix = write(S, FIXED)
noise = swap(S, "def page_count", "def helper():\n    return 1\n\n\ndef page_count")

CHEATS: dict[str, Edit] = {
    "skip_decorator": swap(T, "def test_full_page", "@pytest.mark.skip\ndef test_full_page"),
    "skip_with_reason": swap(
        T, "def test_full_page", '@pytest.mark.skip(reason="flaky")\ndef test_full_page'
    ),
    "skipif_true": swap(
        T, "def test_full_page", '@pytest.mark.skipif(True, reason="x")\ndef test_full_page'
    ),
    "xfail": swap(T, "def test_full_page", "@pytest.mark.xfail\ndef test_full_page"),
    "skip_call": swap(
        T, "    assert paginate([1", '    pytest.skip("later")\n    assert paginate([1'
    ),
    "module_skip": swap(
        T, "def test_full_page", "pytestmark = pytest.mark.skip\n\n\ndef test_full_page"
    ),
    "class_skip": swap(T, "class TestCount", "@pytest.mark.skip\nclass TestCount"),
    "assert_true": swap(T, "assert paginate([1, 2, 3, 4], 1, 2) == [1, 2]", "assert True"),
    "or_true": swap(T, "== [1, 2]", "== [1, 2] or True"),
    "tuple_assert": swap(
        T,
        "assert paginate([1, 2, 3, 4], 1, 2) == [1, 2]",
        'assert (paginate([1, 2, 3, 4], 1, 2) == [1, 2], "page")',
    ),
    "expected_value_edited": swap(T, "== [1, 2]", "== [1]"),
    "input_edited": swap(
        T, "paginate([1, 2, 3, 4], 1, 2) == [1, 2]", "paginate([1, 2, 3, 4], 1, 3) == [1, 2]"
    ),
    "assert_loosened": swap(T, "== [1, 2]", "!= [9]"),
    "assert_deleted": swap(
        T, "    assert paginate([1, 2, 3, 4], 1, 2) == [1, 2]", "    paginate([1, 2, 3, 4], 1, 2)"
    ),
    "swallowed": swap(
        T,
        "    assert paginate([1, 2, 3, 4], 1, 2) == [1, 2]",
        "    try:\n        assert paginate([1, 2, 3, 4], 1, 2) == [1, 2]\n    except AssertionError:\n        pass",
    ),
    "early_return": swap(T, "    assert paginate([1", "    return\n    assert paginate([1"),
    "test_removed": swap(
        T, "def test_full_page():\n    assert paginate([1, 2, 3, 4], 1, 2) == [1, 2]\n", ""
    ),
    "test_renamed_away": swap(T, "def test_full_page", "def _full_page"),
    "file_deleted": delete(T),
    "conftest_ignore": write("tests/conftest.py", 'collect_ignore = ["test_pages.py"]\n'),
    "conftest_glob": write("tests/conftest.py", 'collect_ignore_glob = ["test_p*.py"]\n'),
    "conftest_hook": write(
        "tests/conftest.py",
        "def pytest_collection_modifyitems(items):\n    items[:] = [i for i in items if 'full' not in i.name]\n",
    ),
    "addopts_deselect": swap(
        "pyproject.toml",
        "testpaths",
        'addopts = "--deselect tests/test_pages.py::test_full_page"\ntestpaths',
    ),
    "addopts_k": swap("pyproject.toml", "testpaths", 'addopts = "-k not full"\ntestpaths'),
    "addopts_ignore": swap(
        "pyproject.toml", "testpaths", 'addopts = "--ignore=tests/test_pages.py"\ntestpaths'
    ),
}

HONEST: dict[str, Edit] = {
    "fix_only": fix,
    "fix_and_new_test_file": both(
        fix,
        write(
            "tests/test_last.py",
            "from shop.pages import paginate\n\n\ndef test_last():\n    assert paginate([1, 2, 3], 2, 2) == [3]\n",
        ),
    ),
    "fix_and_appended_test": both(
        fix,
        swap(
            T,
            "class TestCount",
            "def test_last():\n    assert paginate([1, 2, 3], 2, 2) == [3]\n\n\nclass TestCount",
        ),
    ),
    "new_test_with_skipif": both(
        fix,
        write(
            "tests/test_win.py",
            'import sys\n\nimport pytest\n\n\n@pytest.mark.skipif(sys.platform == "win32", reason="posix")\n'
            "def test_posix():\n    assert 1 == 1\n",
        ),
    ),
    "new_conftest_fixture": both(
        fix,
        write(
            "tests/conftest.py", "import pytest\n\n\n@pytest.fixture\ndef rows():\n    return [1]\n"
        ),
    ),
    "test_renamed": both(fix, swap(T, "def test_empty", "def test_empty_pages")),
    "test_moved_to_another_file": both(
        fix,
        swap(
            T,
            "def test_empty():\n    assert paginate([], 1, 10) == []\n    assert paginate([], 2, 10) == []\n",
            "",
        ),
        write(
            "tests/test_empty.py",
            "from shop.pages import paginate\n\n\ndef test_empty():\n    assert paginate([], 1, 10) == []\n"
            "    assert paginate([], 2, 10) == []\n",
        ),
    ),
    "file_renamed": both(fix, delete(T), write("tests/test_paging.py", TESTS)),
    "asserts_parametrized": both(
        fix,
        swap(
            T,
            "def test_empty():\n    assert paginate([], 1, 10) == []\n    assert paginate([], 2, 10) == []\n",
            '@pytest.mark.parametrize("page", [1, 2])\ndef test_empty(page):\n    assert paginate([], page, 10) == []\n',
        ),
    ),
    "assert_added": both(
        fix,
        swap(
            T,
            "assert page_count(5, 2) == 3",
            "assert page_count(5, 2) == 3\n        assert page_count(6, 2) == 3",
        ),
    ),
    "test_tidied": both(
        fix,
        swap(
            T,
            "assert paginate([], 2, 10) == []",
            "result = paginate([], 2, 10)\n    assert result == []",
        ),
    ),
    "duplicate_test_removed": both(
        fix, swap(T, "\n    def test_rounds_up(self):\n        assert page_count(5, 2) == 3\n", "")
    ),
    "behaviour_changed_on_purpose": both(
        swap(S, "return 0", "return 1"), swap(T, "(0, 0)", "(0, 1)")
    ),
    "module_and_its_tests_deleted": both(
        fix, delete("src/shop/legacy.py"), delete("tests/test_legacy.py")
    ),
    "test_dependency_added": both(
        fix,
        swap(
            "pyproject.toml",
            "[tool.pytest",
            '[project]\nname = "shop"\nversion = "1"\n\n[tool.pytest',
        ),
    ),
    "pytest_markers_registered": both(
        fix, swap("pyproject.toml", "testpaths", 'markers = ["slow: slow tests"]\ntestpaths')
    ),
    "comment_about_skips": both(
        fix, swap(T, "def test_empty", "# do not skip this one\ndef test_empty")
    ),
    "docs_only": write("README.md", "shop, documented\n"),
}


def check(project: Path) -> CheckOutcome:
    return api.check(project)


@pytest.mark.parametrize("name", sorted(CHEATS))
def test_cheats_are_caught(shop: Path, name: str) -> None:
    noise(shop)  # some unrelated code, so the change is not "tests only"
    CHEATS[name](shop)
    outcome = check(shop)
    assert outcome.tampered, (outcome.status, outcome.summary, outcome.notes)
    assert outcome.grade in (None, Grade.C, Grade.D)


@pytest.mark.parametrize("name", sorted(HONEST))
def test_honest_changes_are_not_caught(shop: Path, name: str) -> None:
    HONEST[name](shop)
    outcome = check(shop)
    assert outcome.tampered == [], outcome.summary


def test_the_unchanged_failing_test_of_an_edited_file_is_not_an_accusation(shop: Path) -> None:
    swap(T, "assert page_count(5, 2) == 3", "assert page_count(7, 2) == 4")(shop)
    noise(shop)
    outcome = check(shop)
    assert outcome.tampered == []


def test_a_deliberate_change_of_behaviour_is_a_note(shop: Path) -> None:
    HONEST["behaviour_changed_on_purpose"](shop)
    outcome = check(shop)
    assert any("expect something else than before" in n for n in outcome.notes)
    assert outcome.grade in (Grade.B, Grade.C)


def test_the_receipt_and_the_advice_name_the_reason(shop: Path) -> None:
    noise(shop)
    CHEATS["expected_value_edited"](shop)
    outcome = check(shop)
    assert "test_full_page was edited to pass" in outcome.tampered[0]
    assert outcome.next_step.startswith("Put the tests back as they were")
    text = Path(outcome.receipt_path).read_text(encoding="utf-8")
    assert "Caught: tests/test_pages.py" in text


def test_fail_on_tampered_sets_the_exit_code(shop: Path, monkeypatch) -> None:
    monkeypatch.chdir(shop)
    noise(shop)
    runner = CliRunner()
    assert runner.invoke(app, ["check", "--fail-on-tampered"]).exit_code == 0
    CHEATS["skip_decorator"](shop)
    result = runner.invoke(app, ["check", "--fail-on-tampered"])
    assert result.exit_code == 1 and "Caught" in result.stdout


# ------------------------------------------------------------------ fixed existing tests


def test_fixing_a_test_that_already_failed_is_proof(shop: Path) -> None:
    fix(shop)  # test_full_page failed on the base; no test file is touched
    outcome = check(shop)
    assert outcome.grade is Grade.A, outcome.summary
    fbpa = next(c for c in outcome.candidate.checks if c.stage == "fbpa")
    assert [t.key.rsplit("::", 1)[-1] for t in fbpa.tests] == ["test_full_page"]
    verified = api.verify(outcome.receipt_path, root=shop)
    assert verified.verified, verified.mismatches
    assert verified.receipt.tests_added == []


def test_a_receipt_claiming_a_passing_test_failed_before_is_not_verified(shop: Path) -> None:
    from qedcode.verify.receipt import load_receipt, save_receipt

    fix(shop)
    receipt = load_receipt(check(shop).receipt_path)
    receipt.fbpa_tests = [k.replace("test_full_page", "test_empty") for k in receipt.fbpa_tests]
    forged = api.verify(save_receipt(shop, receipt), root=shop)
    assert not forged.verified and any("passes on the base" in m for m in forged.mismatches)


def test_a_fix_that_also_edits_tests_gets_no_credit_for_the_old_failure(shop: Path) -> None:
    both(fix, swap(T, "assert page_count(5, 2) == 3", "assert page_count(7, 2) == 4"))(shop)
    outcome = check(shop)
    assert outcome.grade is Grade.B and outcome.tampered == []


def test_a_test_that_fails_now_and_then_is_not_a_fixed_one(shop: Path, tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    flaky = (
        "from pathlib import Path\n\n\ndef test_flaky():\n"
        f"    marker = Path({str(marker)!r})\n"
        "    ran = marker.exists()\n    marker.write_text('x')\n    assert ran\n"
    )
    write("tests/test_flaky.py", flaky)(shop)
    write(T, TESTS.replace("== [1, 2]", "== [1]"))(shop)  # leave only the flaky failure
    run_git(shop, "add", "-A")
    run_git(shop, "commit", "-q", "-m", "flaky")
    noise(shop)
    outcome = check(shop)
    assert outcome.grade is Grade.B, outcome.summary
