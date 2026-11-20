"""Running JavaScript and TypeScript tests (AGENTS.md section 15.3).

Parsing is tested on real reports from Vitest, Jest and the node reporter (tests/fixtures/js).
`node --test` is run for real (Node is needed, no package install); Vitest and Jest are covered
by the real reports plus a fake runner binary.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

import pytest

from qedcode.config import JS_PROTECTED, ProjectConfig
from qedcode.models.schemas import TestResult
from qedcode.verify import js_runner
from qedcode.verify.js_runner import (
    RawResult,
    is_js_command,
    linked_node_modules,
    parse_jest_report,
    parse_node_report,
    real_command,
    recorded_command,
    run_js_tests,
    selection_from,
    to_outcome,
)
from qedcode.verify.pytest_runner import failed_before, file_level_failure
from qedcode.verify.runners import run_tests

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "js"
PROJ = Path("/proj")
HAVE_NODE = shutil.which("node") is not None
needs_node = pytest.mark.skipif(not HAVE_NODE, reason="Node.js is not installed")


def config(runner: str = "node", **extra) -> ProjectConfig:
    return ProjectConfig(language="javascript", test_runner=runner, protected=JS_PROTECTED, **extra)  # type: ignore[arg-type]


def project(tmp_path: Path, **files: str) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "package.json").write_text('{"name":"p","type":"module"}')
    for rel, text in files.items():
        target = root / rel.replace("__", "/").replace("_dot_", ".")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return root


ADD_BAD = "export function add(a, b) { return a - b; }\n"
ADD_OK = "export function add(a, b) { return a + b; }\n"
TEST_ADD = (
    "import test from 'node:test';\nimport assert from 'node:assert/strict';\n"
    "import { add } from './math.js';\n"
    "test('adds', () => { assert.equal(add(1, 2), 3); });\n"
    "test('adds zero', () => { assert.equal(add(0, 0), 0); });\n"
)


# ------------------------------------------------------------------ reading real reports


def test_a_real_vitest_report() -> None:
    raw = parse_jest_report((FIXTURES / "vitest-report.json").read_text(), PROJ)
    results, bodies = to_outcome(raw)
    assert {r.key: r.outcome for r in results} == {
        "src/broken.test.ts": "error",
        "src/math.test.ts::math add adds": "failed",
        "src/math.test.ts::math add adds zero": "passed",
        "src/math.test.ts::math later": "skipped",
    }
    assert "Cannot find module './missing'" in bodies["src/broken.test.ts"]
    assert "expected -1 to be 3" in bodies["src/math.test.ts::math add adds"]
    assert file_level_failure(["src/broken.test.ts"]) and not file_level_failure(["a::b"])


def test_a_real_jest_report() -> None:
    results, bodies = to_outcome(
        parse_jest_report((FIXTURES / "jest-report.json").read_text(), PROJ)
    )
    assert {r.key: r.outcome for r in results} == {
        "src/broken.test.js": "error",
        "src/math.test.js::math add adds": "failed",
        "src/math.test.js::math add adds zero": "passed",
        "src/math.test.js::math later": "skipped",  # jest calls it "pending"
    }
    assert "Test suite failed to run" in bodies["src/broken.test.js"]
    assert "Expected: 3" in bodies["src/math.test.js::math add adds"]


def test_a_real_node_report() -> None:
    results, bodies = to_outcome(
        parse_node_report((FIXTURES / "node-report.json").read_text(), PROJ)
    )
    assert {r.key: r.outcome for r in results} == {
        "bad.test.js": "error",  # the whole file failed to load
        "math.test.js::adds": "failed",
        "math.test.js::adds zero": "passed",
    }
    assert "Cannot find module" in bodies["bad.test.js"]
    assert "-1 !== 3" in bodies["math.test.js::adds"]


def test_failed_before_treats_a_file_level_failure_as_every_test_failing() -> None:
    results, _ = to_outcome(parse_jest_report((FIXTURES / "vitest-report.json").read_text(), PROJ))
    outcomes = {r.key: r.outcome for r in results}
    assert failed_before("src/broken.test.ts::never", outcomes)  # the file did not even load
    assert failed_before("src/math.test.ts::math add adds", outcomes)


def test_duplicate_names_in_one_file_stay_distinct() -> None:
    raw = [
        RawResult("a.test.js", "same", "passed"),
        RawResult("a.test.js", "same", "failed", "boom", "trace"),
    ]
    results, bodies = to_outcome(raw)
    assert [r.key for r in results] == ["a.test.js::same", "a.test.js::same #2"]
    assert [r.outcome for r in results] == ["passed", "failed"] and list(bodies) == [
        "a.test.js::same #2"
    ]
    assert isinstance(results[0], TestResult)


# ------------------------------------------------------------------ the commands


def test_recorded_commands_have_placeholders_and_a_separator() -> None:
    node = recorded_command(config("node"), ["a.test.js", "b.test.js"])
    assert node == [
        "{node}",
        "--test",
        "--test-reporter",
        "{reporter}",
        "--test-reporter-destination",
        "{report}",
        "--",
        "a.test.js",
        "b.test.js",
    ]
    assert recorded_command(config("vitest"), ["a.test.ts"]) == [
        "{vitest}",
        "run",
        "--reporter=json",
        "--outputFile={report}",
        "--",
        "a.test.ts",
    ]
    jest = recorded_command(config("jest"), [])
    assert jest[0] == "{jest}" and jest[-1] == "--" and "--runTestsByPath" in jest
    assert selection_from(node) == ["a.test.js", "b.test.js"] and selection_from(["pytest"]) == []
    assert (
        is_js_command(node)
        and is_js_command(jest)
        and not is_js_command(["{python}", "-m", "pytest"])
    )


def test_real_commands_use_the_local_binary_and_never_npx(tmp_path: Path) -> None:
    binary = tmp_path / "node_modules" / ".bin" / "vitest"
    report = tmp_path / "r.json"
    vitest = real_command(config("vitest"), "/usr/bin/node", binary, report, ["a.test.ts"])
    assert vitest == [str(binary), "run", "--reporter=json", f"--outputFile={report}", "a.test.ts"]
    jest = real_command(config("jest"), "/usr/bin/node", tmp_path / "jest", report, ["a.test.js"])
    assert jest[1:] == ["--json", f"--outputFile={report}", "--ci", "--runTestsByPath", "a.test.js"]
    node = real_command(config("node"), "/usr/bin/node", None, report, ["a.test.js"])
    assert node[:2] == ["/usr/bin/node", "--test"] and node[-1] == "a.test.js"
    assert str(js_runner.REPORTER) in node and js_runner.REPORTER.is_file()
    assert "npx" not in " ".join([*vitest, *jest, *node])


# ------------------------------------------------------------------ the node_modules link


def test_the_node_modules_link_exists_only_while_tests_run(tmp_path: Path) -> None:
    root = project(tmp_path, node_modules__lib__index_dot_js="x")
    (root / "node_modules" / "lib" / "keep.txt").write_text("precious")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    with linked_node_modules(worktree, root) as made:
        assert made and (worktree / "node_modules").is_symlink()
        assert (worktree / "node_modules" / "lib" / "keep.txt").read_text() == "precious"
    assert not (worktree / "node_modules").exists() and not (worktree / "node_modules").is_symlink()
    assert (root / "node_modules" / "lib" / "keep.txt").read_text() == "precious"


def test_the_link_is_removed_even_when_the_run_fails_and_a_real_folder_is_left_alone(
    tmp_path: Path,
) -> None:
    root = project(tmp_path, node_modules__a__b_dot_js="x")
    worktree = tmp_path / "wt"
    (worktree / "node_modules").mkdir(parents=True)
    (worktree / "node_modules" / "own.txt").write_text("mine")
    with linked_node_modules(worktree, root) as made:
        assert not made  # the worktree already has its own node_modules
    assert (worktree / "node_modules" / "own.txt").read_text() == "mine"
    other = tmp_path / "wt2"
    other.mkdir()
    with pytest.raises(RuntimeError), linked_node_modules(other, root):
        raise RuntimeError("tests blew up")
    assert not (other / "node_modules").is_symlink() and (root / "node_modules").is_dir()
    assert not made


def test_without_a_node_modules_folder_there_is_nothing_to_link(tmp_path: Path) -> None:
    root = project(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    with linked_node_modules(worktree, root) as made:
        assert not made


# ------------------------------------------------------------------ real node --test runs


@needs_node
def test_node_runs_tests_and_reports_pass_and_fail(tmp_path: Path) -> None:
    root = project(tmp_path, math_dot_js=ADD_BAD, math_dot_test_dot_js=TEST_ADD)
    out = run_js_tests(root, config(), root, ["math.test.js"], 60, run_dir=tmp_path / "rd")
    assert out.status == "failed"
    assert {r.key: r.outcome for r in out.results} == {
        "math.test.js::adds": "failed",
        "math.test.js::adds zero": "passed",
    }
    assert "1 of 2 tests failed; first: math.test.js::adds" in out.check.summary
    assert "-1 !== 3" in out.check.details and out.check.stage == "targeted"
    assert out.check.command == recorded_command(config(), ["math.test.js"])
    (root / "math.js").write_text(ADD_OK)
    fixed = run_js_tests(root, config(), root, ["math.test.js"], 60, run_dir=tmp_path / "rd2")
    assert fixed.status == "ok" and fixed.passed_keys == [
        "math.test.js::adds",
        "math.test.js::adds zero",
    ]
    assert fixed.check.summary == "2/2 tests pass."


@needs_node
def test_nested_suites_get_full_names(tmp_path: Path) -> None:
    deep = (
        "import { describe, it } from 'node:test';\nimport assert from 'node:assert/strict';\n"
        "describe('outer', () => { describe('inner', () => {\n"
        "  it('works', () => { assert.ok(true); });\n  it.skip('later', () => {});\n}); });\n"
    )
    root = project(tmp_path, deep_dot_test_dot_js=deep)
    out = run_js_tests(root, config(), root, ["deep.test.js"], 60, run_dir=tmp_path / "rd")
    assert out.status == "ok"
    assert {r.key: r.outcome for r in out.results} == {
        "deep.test.js::outer > inner > works": "passed",
        "deep.test.js::outer > inner > later": "skipped",
    }


@needs_node
def test_a_file_that_cannot_load_is_a_collection_error_with_the_real_reason(tmp_path: Path) -> None:
    root = project(tmp_path, bad_dot_test_dot_js="import { nope } from './missing.js';\n")
    out = run_js_tests(root, config(), root, ["bad.test.js"], 60, run_dir=tmp_path / "rd")
    assert out.status == "collection_error"
    assert [(r.key, r.outcome) for r in out.results] == [("bad.test.js", "error")]
    assert "1 test file(s) could not be loaded: bad.test.js" in out.check.summary
    assert "Cannot find module" in out.check.summary and "missing.js" in out.check.details


@needs_node
def test_an_empty_selection_runs_every_test_file(tmp_path: Path) -> None:
    root = project(
        tmp_path, math_dot_js=ADD_OK, math_dot_test_dot_js=TEST_ADD,
        tests__other_dot_test_dot_js="import test from 'node:test';\ntest('o', () => {});\n",
    )  # fmt: skip
    out = run_js_tests(root, config(), root, None, 60, run_dir=tmp_path / "rd", stage="full")
    assert out.status == "ok" and out.check.stage == "full"
    assert sorted(out.passed_keys) == [
        "math.test.js::adds", "math.test.js::adds zero", "tests/other.test.js::o",
    ]  # fmt: skip


@needs_node
def test_a_project_without_tests_has_nothing_to_run(tmp_path: Path) -> None:
    root = project(tmp_path, math_dot_js=ADD_OK)
    out = run_js_tests(root, config(), root, [], 60, run_dir=tmp_path / "rd")
    assert (
        out.status == "no_tests"
        and out.check.passed
        and out.check.summary == "No tests were collected."
    )


@needs_node
def test_a_slow_test_times_out_and_is_stopped(tmp_path: Path) -> None:
    slow = "import test from 'node:test';\ntest('slow', async () => { await new Promise(r => setTimeout(r, 60000)); });\n"
    root = project(tmp_path, slow_dot_test_dot_js=slow)
    started = time.monotonic()
    out = run_js_tests(root, config(), root, ["slow.test.js"], 2, run_dir=tmp_path / "rd")
    assert out.status == "timeout" and time.monotonic() - started < 30
    assert "did not finish within 2 seconds" in out.check.summary


@needs_node
def test_a_cancel_stops_the_run(tmp_path: Path) -> None:
    slow = "import test from 'node:test';\ntest('slow', async () => { await new Promise(r => setTimeout(r, 60000)); });\n"
    root = project(tmp_path, slow_dot_test_dot_js=slow)
    ticks = iter(range(10_000))
    out = run_js_tests(
        root,
        config(),
        root,
        ["slow.test.js"],
        60,
        run_dir=tmp_path / "rd",
        cancelled=lambda: next(ticks) > 10,
    )
    assert out.status == "cancelled"


@needs_node
def test_the_tests_can_see_node_modules_while_they_run(tmp_path: Path) -> None:
    root = project(tmp_path)
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "package.json").write_text('{"name":"pkg","main":"index.js"}')
    (root / "node_modules" / "pkg" / "index.js").write_text("module.exports = { answer: 42 };\n")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / "package.json").write_text('{"name":"p","type":"commonjs"}')
    (worktree / "uses.test.js").write_text(
        "const test = require('node:test');\nconst assert = require('node:assert/strict');\n"
        "const { answer } = require('pkg');\ntest('dep', () => { assert.equal(answer, 42); });\n"
    )
    out = run_js_tests(worktree, config(), root, ["uses.test.js"], 60, run_dir=tmp_path / "rd")
    assert out.status == "ok", out.check.details
    assert not (worktree / "node_modules").exists()  # gone again, so a commit would not record it
    assert (root / "node_modules" / "pkg" / "index.js").exists()


@needs_node
def test_node_is_scrubbed_of_secrets_and_runs_in_ci_mode(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MY_API_KEY", "secret-value")
    probe = (
        "import test from 'node:test';\nimport assert from 'node:assert/strict';\n"
        "test('env', () => {\n  assert.equal(process.env.MY_API_KEY, undefined);\n"
        "  assert.equal(process.env.CI, 'true');\n});\n"
    )
    root = project(tmp_path, env_dot_test_dot_js=probe)
    out = run_js_tests(root, config(), root, ["env.test.js"], 60, run_dir=tmp_path / "rd")
    assert out.status == "ok", out.check.details


# ------------------------------------------------------------------ vitest and jest through a fake binary


def fake_binary(root: Path, name: str, report: dict | str | None, exit_code: int = 0) -> None:
    """A stand-in `node_modules/.bin/<name>`: writes `report` to the requested file."""
    import json

    bin_dir = root / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / name
    body = (
        f"#!{sys.executable}\nimport json, sys\n"
        "out = next(a.split('=', 1)[1] for a in sys.argv if a.startswith('--outputFile='))\n"
        f"report = {(report if isinstance(report, str) else json.dumps(report))!r}\n"
        "open(out, 'w').write(report)\n"
        f"sys.exit({exit_code})\n"
    )
    script.write_text(body)
    script.chmod(0o755)


@pytest.mark.skipif(sys.platform == "win32", reason="the fake binary is a POSIX script")
@pytest.mark.parametrize("runner", ["vitest", "jest"])
def test_vitest_and_jest_run_from_node_modules_and_results_are_read(
    tmp_path: Path, runner: str
) -> None:
    root = project(tmp_path)
    fixture = FIXTURES / f"{runner}-report.json"
    fake_binary(root, runner, fixture.read_text().replace("/proj", str(root)), exit_code=1)
    shutil.copy(__file__, root / "unused.txt")
    out = run_js_tests(
        root, config(runner), root, ["src/math.test.ts"], 60, run_dir=tmp_path / "rd"
    )
    assert out.status == "failed" and out.exit_code == 1
    assert out.check.command[0] == "{" + runner + "}"
    assert [r.key for r in out.failing][:1] in (["src/broken.test.ts"], ["src/broken.test.js"])
    assert any(
        r.key.endswith("::math add adds zero") and r.outcome == "passed" for r in out.results
    )


@pytest.mark.skipif(sys.platform == "win32", reason="the fake binary is a POSIX script")
def test_a_missing_runner_says_how_to_install_it_without_installing(tmp_path: Path) -> None:
    root = project(tmp_path)
    out = run_js_tests(
        root,
        config("vitest", package_manager="pnpm"),
        root,
        ["a.test.ts"],
        60,
        run_dir=tmp_path / "rd",
    )
    assert out.status == "error"
    assert "vitest is not installed in node_modules" in out.check.summary
    assert (
        "pnpm install" in out.check.summary and "never installs Node packages" in out.check.summary
    )
    assert not (root / "node_modules").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="the fake binary is a POSIX script")
def test_an_empty_or_broken_report_is_an_error_not_a_pass(tmp_path: Path) -> None:
    root = project(tmp_path)
    fake_binary(root, "vitest", "", exit_code=1)
    out = run_js_tests(root, config("vitest"), root, ["a.test.ts"], 60, run_dir=tmp_path / "rd")
    assert out.status == "error" and "stopped without a report (exit 1)" in out.check.summary
    fake_binary(root, "jest", {"not": "a report"}, exit_code=0)
    garbled = run_js_tests(root, config("jest"), root, ["a.test.js"], 60, run_dir=tmp_path / "rd2")
    assert garbled.status == "no_tests"  # valid JSON without results: nothing ran
    (root / "node_modules" / ".bin" / "jest").write_text(
        f"#!{sys.executable}\nimport sys\nopen([a.split('=',1)[1] for a in sys.argv if a.startswith('--outputFile=')][0],'w').write('{{not json')\n"
    )
    bad = run_js_tests(root, config("jest"), root, ["a.test.js"], 60, run_dir=tmp_path / "rd3")
    assert bad.status == "error" and "could not be read" in bad.check.summary


def test_without_node_the_error_says_what_to_install(tmp_path: Path, monkeypatch) -> None:
    root = project(tmp_path)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    out = run_js_tests(root, config(), root, ["a.test.js"], 60, run_dir=tmp_path / "rd")
    assert out.status == "error" and "Node.js was not found" in out.check.summary


# ------------------------------------------------------------------ the dispatcher


def test_run_tests_picks_the_runner_by_language(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []

    def fake_js(*args, **kwargs):
        calls.append("js")
        return "js-outcome"

    def fake_py(*args, **kwargs):
        calls.append("py")
        return "py-outcome"

    monkeypatch.setattr("qedcode.verify.runners.run_js_tests", fake_js)
    monkeypatch.setattr("qedcode.verify.runners.run_pytest", fake_py)
    assert run_tests(tmp_path, config(), tmp_path, ["a"], 5) == "js-outcome"
    assert (
        run_tests(tmp_path, ProjectConfig(python=sys.executable), tmp_path, ["a"], 5)
        == "py-outcome"
    )
    assert calls == ["js", "py"]


def test_python_projects_still_run_pytest_through_the_dispatcher(tmp_path: Path) -> None:
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    out = run_tests(
        tmp_path,
        ProjectConfig(python=sys.executable),
        tmp_path,
        ["test_ok.py"],
        60,
        run_dir=tmp_path / "rd",
    )
    assert out.status == "ok" and out.passed_keys == ["test_ok::test_ok"]


def test_the_sealed_test_box_is_for_python_projects_only(tmp_path: Path) -> None:
    from qedcode.verify.sandbox import sandbox_for

    assert sandbox_for(config(sandbox="docker"), tmp_path) is None
    assert sandbox_for(ProjectConfig(sandbox="docker"), tmp_path) is not None
    assert os.name  # keep the import used
