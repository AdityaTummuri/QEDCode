from __future__ import annotations

import sys
import time
from pathlib import Path

import psutil
import pytest

from qedcode.config import ProjectConfig
from qedcode.project.context import (
    imports_module,
    module_names,
    related_tests,
    select_targeted,
)
from qedcode.vcs import worktree
from qedcode.vcs.snapshot import snapshot
from qedcode.verify.baseline import baseline, baseline_run
from qedcode.verify.pytest_runner import parse_junit, pythonpath_for, run_pytest
from tests.conftest import make_repo, run_git, write_files

PY = sys.executable
PYTEST_CFG = '[tool.pytest.ini_options]\npythonpath = ["src"]\ntestpaths = ["tests"]\n'


def small_project(tmp_path: Path, tests: dict[str, str], src: dict[str, str] | None = None) -> Path:
    files = {"pyproject.toml": PYTEST_CFG, "src/pkg/__init__.py": ""}
    files.update({f"src/pkg/{n}": c for n, c in (src or {"m.py": "VALUE = 1\n"}).items()})
    files.update({f"tests/{n}": c for n, c in tests.items()})
    root = tmp_path / "proj"
    root.mkdir()
    write_files(root, files)
    return root


# ------------------------------------------------------------------ run_pytest on bug zoo copies


def test_zoo_001_visible_tests_all_pass(zoo_copy) -> None:
    project = zoo_copy("001_paginate_off_by_one")
    out = run_pytest(project, PY, None, 120)
    assert out.status == "ok" and out.check.passed
    assert len(out.results) == 5 and all(r.outcome == "passed" for r in out.results)
    assert "tests.test_pages::test_empty_items_give_an_empty_page" in out.as_map()
    assert out.check.stage == "targeted" and out.check.exit_code == 0
    assert out.check.command[:3] == [PY, "-m", "pytest"] and "--junitxml" in out.check.command
    assert "5/5 tests pass" in out.check.summary
    assert out.check.duration_s >= 0 and out.exit_code == 0


def test_hidden_tests_fail_on_the_buggy_code_with_a_helpful_report(zoo_copy) -> None:
    project = zoo_copy("001_paginate_off_by_one")
    out = run_pytest(project, PY, ["hidden/test_hidden.py"], 120, stage="targeted")
    assert out.status == "failed" and not out.check.passed
    assert sorted(r.outcome for r in out.results) == ["failed", "failed", "passed"]
    assert "2 of 3 tests failed" in out.check.summary
    assert out.failing[0].message  # the first failure line
    assert "assert" in out.check.details and "paginate" in out.check.details
    assert len(out.check.details) <= 4000 and len(out.check.summary) <= 300
    assert out.check.details.count("FAILED") <= 3


def test_selection_by_file_and_by_node_id(zoo_copy) -> None:
    project = zoo_copy("001_paginate_off_by_one")
    one_file = run_pytest(project, PY, ["tests/test_pages.py"], 120)
    assert len(one_file.results) == 5
    node = run_pytest(project, PY, ["tests/test_pages.py::test_page_count_rounds_up"], 120)
    assert [r.key for r in node.results] == ["tests.test_pages::test_page_count_rounds_up"]


def test_a_run_dir_keeps_the_report_and_the_log(zoo_copy, tmp_path: Path) -> None:
    project = zoo_copy("002_discount_threshold")
    run_dir = tmp_path / "runs" / "t-1"
    out = run_pytest(project, PY, None, 120, stage="full", run_dir=run_dir)
    assert (run_dir / "report-full.xml").exists() and (run_dir / "full.log").exists()
    assert out.check.log_path == str(run_dir / "full.log") and out.check.stage == "full"
    assert not (project / ".pytest_cache").exists()  # -p no:cacheprovider


# ------------------------------------------------------------------ odd outcomes


def test_no_tests_collected(tmp_path: Path) -> None:
    root = small_project(tmp_path, {"helper.py": "X = 1\n"})
    out = run_pytest(root, PY, None, 60)
    assert out.status == "no_tests" and out.results == []
    assert out.check.passed and "No tests were collected" in out.check.summary


def test_collection_errors_are_failures(tmp_path: Path) -> None:
    root = small_project(
        tmp_path,
        {
            "test_ok.py": "def test_ok():\n    assert True\n",
            "test_bad.py": "import not_a_real_module\n",
        },
    )
    out = run_pytest(root, PY, None, 60)
    assert out.status == "collection_error" and not out.check.passed
    assert any(r.outcome == "error" for r in out.results)
    assert "could not be collected" in out.check.summary
    assert "not_a_real_module" in out.check.details


def test_a_syntax_error_in_a_test_file(tmp_path: Path) -> None:
    root = small_project(tmp_path, {"test_syntax.py": "def test_x(:\n    pass\n"})
    out = run_pytest(root, PY, None, 60)
    assert out.status == "collection_error" and not out.check.passed


def test_skipped_xfail_and_parametrized_tests(tmp_path: Path) -> None:
    root = small_project(
        tmp_path,
        {
            "test_kinds.py": (
                "import pytest\n"
                "@pytest.mark.skip(reason='later')\n"
                "def test_skipped():\n    pass\n"
                "@pytest.mark.xfail\n"
                "def test_expected_failure():\n    assert False\n"
                "@pytest.mark.parametrize('n', [1, 2])\n"
                "def test_param(n):\n    assert n\n"
                "class TestGroup:\n    def test_in_class(self):\n        assert True\n"
            )
        },
    )
    out = run_pytest(root, PY, None, 60)
    results = out.as_map()
    assert results["tests.test_kinds::test_skipped"] == "skipped"
    assert results["tests.test_kinds::test_expected_failure"] == "skipped"
    assert results["tests.test_kinds::test_param[1]"] == "passed"
    assert results["tests.test_kinds.TestGroup::test_in_class"] == "passed"
    assert out.status == "ok"


def test_timeouts_kill_the_run_and_every_child(tmp_path: Path) -> None:
    pidfile = tmp_path / "child.pid"
    sleeper = (
        "import subprocess, sys, time\n"
        f"p = open({str(pidfile)!r}, 'w')\n"
        "def test_sleeps():\n"
        "    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "    p.write(str(child.pid)); p.flush()\n"
        "    time.sleep(120)\n"
    )
    root = small_project(tmp_path, {"test_slow.py": sleeper})
    started = time.monotonic()
    out = run_pytest(root, PY, None, 3)
    assert time.monotonic() - started < 30
    assert out.status == "timeout" and not out.check.passed
    assert "did not finish within 3 seconds" in out.check.summary
    assert "test_timeout_seconds" in out.check.details
    deadline = time.monotonic() + 5
    pid = int(pidfile.read_text())
    while psutil.pid_exists(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    alive = psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    assert not alive, "the test's child process survived the timeout"


def test_pytest_missing_in_the_interpreter(tmp_path: Path) -> None:
    root = small_project(tmp_path, {"test_a.py": "def test_a():\n    pass\n"})
    prefix = [PY, "-c", "import sys; print('No module named pytest', file=sys.stderr); sys.exit(1)"]
    out = run_pytest(root, "python-without-pytest", None, 30, command_prefix=prefix)
    assert out.status == "error" and not out.check.passed
    assert "pytest is not installed in python-without-pytest" in out.check.summary


def test_an_interpreter_that_cannot_start(tmp_path: Path) -> None:
    root = small_project(tmp_path, {"test_a.py": "def test_a():\n    pass\n"})
    out = run_pytest(root, str(tmp_path / "no-such-python"), None, 30)
    assert out.status == "error" and "could not start" in out.check.summary


def test_cancel(tmp_path: Path) -> None:
    root = small_project(
        tmp_path, {"test_slow.py": "import time\ndef test_s():\n    time.sleep(60)\n"}
    )
    calls = {"n": 0}

    def cancelled() -> bool:
        calls["n"] += 1
        return calls["n"] > 8

    out = run_pytest(root, PY, None, 60, cancelled=cancelled)
    assert out.status == "cancelled" and not out.check.passed


def test_the_worktrees_own_code_beats_an_installed_copy(tmp_path: Path, monkeypatch) -> None:
    stale = tmp_path / "stale" / "pkg"
    stale.mkdir(parents=True)
    (stale / "__init__.py").write_text("")
    (stale / "m.py").write_text("VALUE = 'stale user copy'\n")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "stale"))
    root = small_project(
        tmp_path,
        {"test_m.py": "from pkg.m import VALUE\ndef test_value():\n    assert VALUE == 2\n"},
        src={"m.py": "VALUE = 2\n"},
    )
    (root / "pyproject.toml").write_text("")  # no pythonpath help from the project itself
    out = run_pytest(root, PY, None, 60)
    assert out.status == "ok", out.check.details
    assert pythonpath_for(root).split(__import__("os").pathsep)[:2] == [
        str(root / "src"),
        str(root),
    ]


def test_secrets_in_the_environment_never_reach_the_tests(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MY_SECRET_TOKEN", "hunter2-hunter2")
    root = small_project(
        tmp_path,
        {
            "test_env.py": "import os\ndef test_no_secrets():\n    assert 'MY_SECRET_TOKEN' not in os.environ\n"
        },
    )
    assert run_pytest(root, PY, None, 60).status == "ok"


def test_parse_junit_directly() -> None:
    xml = (
        '<testsuites><testsuite name="pytest">'
        '<testcase classname="a.b" name="test_ok" time="0.1"/>'
        '<testcase classname="a.b" name="test_bad"><failure message="assert 1 == 2">trace\nline 2</failure></testcase>'
        '<testcase classname="" name="tests/test_c.py"><error message="ImportError: x">boom</error></testcase>'
        '<testcase classname="a.b" name="test_skip"><skipped message="why"/></testcase>'
        "</testsuite></testsuites>"
    )
    results, details = parse_junit(xml)
    assert [(r.key, r.outcome) for r in results] == [
        ("a.b::test_ok", "passed"),
        ("a.b::test_bad", "failed"),
        ("tests/test_c.py", "error"),
        ("a.b::test_skip", "skipped"),
    ]
    assert results[1].message == "assert 1 == 2"
    assert "FAILED a.b::test_bad" in details and "ERROR tests/test_c.py" in details


# ------------------------------------------------------------------ baseline


def zoo_repo(zoo_copy, name: str, tmp_path: Path):
    project = zoo_copy(name)
    import shutil

    shutil.rmtree(project / "hidden")
    (project / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n")
    run_git(project, "init", "-q", "-b", "main")
    run_git(project, "add", "-A")
    run_git(project, "commit", "-q", "-m", "buggy")
    return project, snapshot(project, "t-base")


def test_baseline_on_zoo_001_lists_every_visible_test_as_passed(zoo_copy, tmp_path: Path) -> None:
    project, snap = zoo_repo(zoo_copy, "001_paginate_off_by_one", tmp_path)
    base = baseline(project, snap, ProjectConfig(python=PY), full=True)
    assert base.commit == snap and base.full and base.runnable and base.reason == ""
    assert len(base.results) == 5
    assert set(base.results.values()) == {"passed"}
    assert worktree.list_worktrees(project) == []  # its own worktree is gone
    assert run_git(project, "status", "--porcelain").strip() == ""  # user's repo untouched


def test_baseline_run_reports_how_long_the_full_suite_took(zoo_copy, tmp_path: Path) -> None:
    project, snap = zoo_repo(zoo_copy, "002_discount_threshold", tmp_path)
    run = baseline_run(project, snap, ProjectConfig(python=PY), full=True)
    assert run.full_seconds is not None and run.full_seconds > 0
    assert run.outcome is not None and run.outcome.status == "ok"


def test_baseline_with_only_related_tests(zoo_copy, tmp_path: Path) -> None:
    project, snap = zoo_repo(zoo_copy, "001_paginate_off_by_one", tmp_path)
    run = baseline_run(
        project, snap, ProjectConfig(python=PY), full=False, related=["tests/test_pages.py"]
    )
    assert run.baseline.full is False and len(run.baseline.results) == 5
    assert run.full_seconds is None
    nothing = baseline_run(project, snap, ProjectConfig(python=PY), full=False, related=[])
    assert nothing.baseline.runnable and nothing.baseline.results == {}
    assert "No related tests" in nothing.baseline.reason


def test_baseline_records_known_failures(tmp_path: Path) -> None:
    root = make_repo(
        tmp_path / "r",
        {
            "pyproject.toml": PYTEST_CFG,
            "src/pkg/__init__.py": "",
            "tests/test_known.py": "def test_good():\n    assert True\ndef test_known_bad():\n    assert False\n",
        },
    )
    snap = snapshot(root, "t-k")
    base = baseline(root, snap, ProjectConfig(python=PY), full=True)
    assert base.runnable
    assert base.results["tests.test_known::test_good"] == "passed"
    assert base.results["tests.test_known::test_known_bad"] == "failed"


def test_baseline_is_not_runnable_when_pytest_cannot_run(tmp_path: Path) -> None:
    root = make_repo(
        tmp_path / "r", {"a.py": "A = 1\n", "tests/test_a.py": "def test_a():\n    pass\n"}
    )
    snap = snapshot(root, "t-n")
    base = baseline(root, snap, ProjectConfig(python=str(tmp_path / "missing-python")), full=True)
    assert not base.runnable and "could not start" in base.reason and base.results == {}


def test_baseline_timeout_makes_it_not_runnable(tmp_path: Path) -> None:
    root = make_repo(
        tmp_path / "r",
        {"tests/test_slow.py": "import time\ndef test_s():\n    time.sleep(60)\n"},
    )
    snap = snapshot(root, "t-t")
    cfg = ProjectConfig(python=PY, test_timeout_seconds=2)
    base = baseline(root, snap, cfg, full=True)
    assert not base.runnable and "did not finish" in base.reason
    assert worktree.list_worktrees(root) == []


def test_a_custom_test_command_is_honoured(zoo_copy, tmp_path: Path) -> None:
    project, snap = zoo_repo(zoo_copy, "003_mutable_default", tmp_path)
    cfg = ProjectConfig(python=PY, test_command=["{python}", "-m", "pytest", "-k", "has_tag"])
    base = baseline(project, snap, cfg, full=True)
    assert list(base.results) == ["tests.test_tags::test_has_tag"]


# ------------------------------------------------------------------ related tests (selection rules)


@pytest.fixture
def layout(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    write_files(
        root,
        {
            "src/shop/__init__.py": "",
            "src/shop/pages.py": "def paginate(): ...\n",
            "src/shop/cart.py": "class Cart: ...\n",
            "src/shop/util/__init__.py": "",
            "src/shop/util/money.py": "def fmt(): ...\n",
            "tests/test_pages.py": "from shop.pages import paginate\n",
            "tests/test_checkout.py": "from shop import cart\n\ndef test_x():\n    cart.Cart()\n",
            "tests/test_money_fmt.py": "import shop.util.money\n",
            "tests/test_unrelated.py": "import os\n",
            "tests/cart_test.py": "def test_c():\n    pass\n",
            "tests/test_bare.py": "import shop\n\ndef test_b():\n    shop.cart.Cart()\n",
        },
    )
    return root


def test_module_names(layout: Path) -> None:
    assert module_names("src/shop/pages.py", layout)[0] == "shop.pages"
    assert module_names("src/shop/util/money.py", layout)[0] == "shop.util.money"
    assert module_names("src/shop/__init__.py", layout)[0] == "shop"
    assert "src.shop.pages" in module_names("src/shop/pages.py", layout)
    assert module_names("README.md", layout) == []
    assert module_names("setup.py", layout) == ["setup"]


def test_related_tests_by_name(layout: Path) -> None:
    assert "tests/test_pages.py" in related_tests(["src/shop/pages.py"], layout)
    assert "tests/cart_test.py" in related_tests(["src/shop/cart.py"], layout)


def test_related_tests_by_import_forms(layout: Path) -> None:
    cart = related_tests(["src/shop/cart.py"], layout)
    assert "tests/test_checkout.py" in cart  # from shop import cart
    assert "tests/test_bare.py" in cart  # import shop ... shop.cart.Cart
    assert "tests/test_money_fmt.py" in related_tests(
        ["src/shop/util/money.py"], layout
    )  # import a.b.c
    assert "tests/test_unrelated.py" not in cart
    assert "tests/test_pages.py" not in cart


def test_a_changed_package_init_selects_importers_of_the_package(layout: Path) -> None:
    selected = related_tests(["src/shop/__init__.py"], layout)
    assert "tests/test_bare.py" in selected


def test_a_changed_test_file_selects_itself_and_non_python_changes_select_nothing(
    layout: Path,
) -> None:
    assert related_tests(["tests/test_unrelated.py"], layout) == ["tests/test_unrelated.py"]
    assert related_tests(["README.md", "data.json"], layout) == []


def test_imports_module_patterns() -> None:
    assert imports_module("import shop.pages\n", ["shop.pages"])
    assert imports_module("from shop.pages import x\n", ["shop.pages"])
    assert imports_module("from shop import cart, pages\n", ["shop.pages"])
    assert imports_module("  from shop import (pages)\n", ["shop.pages"])
    assert not imports_module("from shop import cart\n", ["shop.pages"])
    assert not imports_module("# import shop.pages\n", ["shop.pages"])
    assert not imports_module("import shop.pages_extra\n", ["shop.pages"])
    assert imports_module("import pages\n", ["pages"])


def test_select_targeted_follows_the_four_rules(layout: Path) -> None:
    sel = select_targeted(["src/shop/pages.py"], layout, repro_files=["tests/test_qed_x.py"])
    assert sel[0] == "tests/test_qed_x.py" and "tests/test_pages.py" in sel  # 1 then 2
    assert select_targeted(["src/shop/cart.py"], layout)[:1] == [
        "tests/cart_test.py"
    ] or "tests/test_checkout.py" in select_targeted(["src/shop/cart.py"], layout)
    # nothing matched: a fast baseline means "run everything" (None), a slow or unknown one means none
    nothing = ["src/shop/orphan.py"]
    assert select_targeted(nothing, layout, baseline_full_seconds=12.0) is None
    assert select_targeted(nothing, layout, baseline_full_seconds=60.0) is None
    assert select_targeted(nothing, layout, baseline_full_seconds=61.0) == []
    assert select_targeted(nothing, layout) == []
    # a repro test alone is enough: no full-suite fallback
    assert select_targeted(
        nothing, layout, repro_files=["tests/test_qed_y.py"], baseline_full_seconds=1.0
    ) == ["tests/test_qed_y.py"]


def test_related_tests_on_every_zoo_project_find_the_visible_test(zoo_copy) -> None:
    from tests.conftest import zoo_project_names

    for name in zoo_project_names():
        project = zoo_copy(name)
        src = next((project / "src").rglob("*.py"))
        changed = [
            p.relative_to(project).as_posix()
            for p in (project / "src").rglob("*.py")
            if p.name != "__init__.py"
        ]
        found = related_tests(changed, project)
        assert any(f.startswith("tests/") for f in found), (name, found)
        assert src.exists()


def test_file_level_failures_count_as_failing_before() -> None:
    from qedcode.verify.pytest_runner import failed_before, file_level_failure

    assert file_level_failure(["tests/test_x.py"]) and not file_level_failure(["t.a::test_b"])
    # a feature test whose import fails on the base: no individual test could run
    base = {"tests/test_qed_x.py": "error"}
    assert failed_before("tests.test_qed_x::test_new", base)
    assert failed_before("t.a::b", {"t.a::b": "failed"})
    assert not failed_before("t.a::b", {"t.a::b": "passed", "t.a::c": "failed"})
    assert not failed_before("t.a::b", {})
