"""JavaScript and TypeScript project support: detection, paths, guards, map, related tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qedcode.config import JS_PROTECTED, ProjectConfig
from qedcode.models.schemas import Speed, TaskSpec
from qedcode.project.context import (
    build_context,
    js_test_extension,
    related_tests,
    repro_test_path,
    select_targeted,
)
from qedcode.project.detect import (
    ProjectDetection,
    count_js_tests,
    detect_project,
    find_test_files,
    first_run,
)
from qedcode.project.detect_js import (
    choose_language,
    detect_js,
    find_js_test_files,
    js_config,
    package_manager_for,
    resolve_js_import,
    runner_binary,
    runner_for,
    version_tuple,
)
from qedcode.project.paths import is_inert_path, is_js_path, is_protected, is_test_file
from qedcode.project.repo_map import list_source_files, repo_map, top_level_symbols_js
from qedcode.verify.guard import risk_check, risk_findings, scope_check
from qedcode.verify.lint import parse_check
from qedcode.verify.run import RunResult

NODE = "/usr/bin/node"


def which_node(name: str) -> str | None:
    return NODE if name == "node" else None


def node_probe(version: str = "22.23.5"):
    def probe(args: list[str], cwd: Path) -> RunResult:
        return RunResult(
            args=args, exit_code=0, stdout=f"v{version}\n" if args[0] == NODE else "1.2.3\n"
        )

    return probe


def write(root: Path, **files: str) -> Path:
    for rel, text in files.items():
        path = root / rel.replace("__", "/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def package(root: Path, **fields) -> None:
    (root / "package.json").write_text(json.dumps({"name": "demo", **fields}))


def diff_adding(
    path: str, lines: list[str], *, new: bool = False, removed: list[str] | None = None
) -> str:
    head = f"diff --git a/{path} b/{path}\n"
    head += "new file mode 100644\n--- /dev/null\n" if new else f"--- a/{path}\n"
    head += f"+++ b/{path}\n@@ -1,{len(removed or [])} +1,{len(lines)} @@\n"
    return head + "".join(f"-{r}\n" for r in removed or []) + "".join(f"+{ln}\n" for ln in lines)


# ------------------------------------------------------------------ paths


@pytest.mark.parametrize(
    "path",
    [
        "src/math.test.ts", "src/math.spec.js", "src/__tests__/math.js", "tests/helper.js",
        "test/a.mjs", "pkg/a/b.test.tsx", "tests/test_x.py", "test_y.py", "a/y_test.py",
    ],
)  # fmt: skip
def test_test_files_are_recognised(path: str) -> None:
    assert is_test_file(path)


@pytest.mark.parametrize(
    "path", ["src/math.ts", "src/index.js", "lib/spec.js", "README.md", "src/a.py"]
)
def test_source_files_are_not_tests(path: str) -> None:
    assert not is_test_file(path)


def test_is_js_path() -> None:
    assert is_js_path("a.TS") and is_js_path("x/y.mjs") and not is_js_path("a.py")


def test_js_protected_defaults() -> None:
    for path in ("src/a.test.ts", "tests/x.js", "package.json", "pnpm-lock.yaml", "tsconfig.build.json",
                 "vitest.config.ts", "jest.config.js", "src/__tests__/a.js", ".github/ci.yml"):  # fmt: skip
        assert is_protected(path, JS_PROTECTED), path
    for path in ("src/math.ts", "src/index.js", "lib/util.mjs"):
        assert not is_protected(path, JS_PROTECTED), path


# ------------------------------------------------------------------ detection


def test_runner_and_package_manager_come_from_package_json(tmp_path: Path) -> None:
    assert runner_for({"devDependencies": {"vitest": "^2"}}) == "vitest"
    assert runner_for({"devDependencies": {"jest": "^29"}}) == "jest"
    assert runner_for({"devDependencies": {"ts-jest": "^29"}}) == "jest"
    assert runner_for({"scripts": {"test": "vitest run"}}) == "vitest"
    assert runner_for({"scripts": {"test": "node --test"}}) == "node"
    assert runner_for({}) == "node"
    assert package_manager_for(tmp_path, {"packageManager": "pnpm@9.1.0"}) == "pnpm"
    write(tmp_path, **{"yarn.lock": ""})
    assert package_manager_for(tmp_path, {"name": "x"}) == "yarn"
    assert package_manager_for(tmp_path / "nothing", {"name": "x"}) == "npm"
    assert package_manager_for(tmp_path / "nothing", {}) == ""


def test_test_files_are_found_and_node_modules_is_skipped(tmp_path: Path) -> None:
    write(
        tmp_path,
        **{
            "src__a.test.ts": "", "src__b.spec.js": "", "src__c.js": "", "src__d.d.ts": "",
            "src__nested__e.test.mjs": "", "tests__helper.js": "", "lib__ok.js": "",
            "node_modules__x__y.test.js": "",
        },
    )  # fmt: skip
    (tmp_path / "__tests__").mkdir()
    (tmp_path / "__tests__" / "only.js").write_text("")
    assert find_js_test_files(tmp_path) == [
        "__tests__/only.js", "src/a.test.ts", "src/b.spec.js", "src/nested/e.test.mjs",
        "tests/helper.js",
    ]  # fmt: skip
    assert find_test_files(tmp_path, "javascript") == find_js_test_files(tmp_path)


def test_language_choice(tmp_path: Path) -> None:
    py_tests = find_test_files
    plain = tmp_path / "plain"
    plain.mkdir()
    assert choose_language(plain, py_tests) == "python"  # no package.json
    node = write(tmp_path / "node", **{"a.test.js": ""})
    package(node)
    assert choose_language(node, py_tests) == "javascript"
    both = write(
        tmp_path / "both", **{"pyproject.toml": "[project]\nname='x'\n", "tests__test_x.py": ""}
    )
    package(both)
    assert choose_language(both, py_tests) == "python"  # a Python project with a package.json
    js_heavy = write(
        tmp_path / "jsheavy", **{"pyproject.toml": "[project]\nname='x'\n", "src__a.test.ts": ""}
    )
    package(js_heavy)
    assert choose_language(js_heavy, py_tests) == "javascript"  # JS tests, no Python tests
    only_py_marker = write(tmp_path / "marker", **{"requirements.txt": ""})
    package(only_py_marker)
    assert choose_language(only_py_marker, py_tests) == "python"


def test_detect_node_runner(tmp_path: Path) -> None:
    write(tmp_path, **{"src__a.test.js": "test('x', () => {});\n"})
    package(tmp_path, scripts={"test": "node --test"})
    found = detect_js(tmp_path, which=which_node, probe=node_probe())
    assert found.runner == "node" and found.runner_ok and found.node_version == "22.23.5"
    assert found.package_manager == "npm" and not found.typescript
    assert found.test_files == ["src/a.test.js"] and found.notes == []


def test_detect_vitest_needs_it_in_node_modules(tmp_path: Path) -> None:
    write(tmp_path, **{"src__a.test.ts": ""})
    package(tmp_path, devDependencies={"vitest": "^2"}, packageManager="pnpm@9")
    missing = detect_js(tmp_path, which=which_node, probe=node_probe())
    assert missing.runner == "vitest" and not missing.runner_ok and missing.typescript
    assert any("not installed in node_modules" in n and "pnpm install" in n for n in missing.notes)
    assert any("never installs Node packages" in n for n in missing.notes)
    write(tmp_path, **{"node_modules__.bin__vitest": "#!/bin/sh\n"})
    assert runner_binary(tmp_path, "vitest") is not None
    present = detect_js(tmp_path, which=which_node, probe=node_probe())
    assert present.runner_ok and present.runner_version == "1.2.3" and present.notes == []


def test_detect_without_node_or_with_an_old_one(tmp_path: Path) -> None:
    write(tmp_path, **{"a.test.js": ""})
    package(tmp_path)
    none = detect_js(tmp_path, which=lambda name: None)
    assert not none.runner_ok and any("Node.js was not found" in n for n in none.notes)
    old = detect_js(tmp_path, which=which_node, probe=node_probe("18.19.0"))
    assert any("too old" in n and "Node 20" in n for n in old.notes)
    ts = write(tmp_path / "ts", **{"a.test.ts": ""})
    package(ts)
    early = detect_js(ts, which=which_node, probe=node_probe("22.6.0"))
    assert any("22.18" in n for n in early.notes)
    assert not any(
        "22.18" in n for n in detect_js(ts, which=which_node, probe=node_probe("22.20.1")).notes
    )
    assert version_tuple("v22.18.3") == (22, 18) and version_tuple("junk") == (0, 0)


def test_detect_project_builds_a_javascript_config(tmp_path: Path) -> None:
    write(tmp_path, **{"src__a.test.js": "it('a', () => {});\ntest('b', () => {});\n"})
    package(tmp_path)
    detection = detect_project(tmp_path, which=which_node)
    config = detection.config
    assert detection.language == "javascript" and detection.runner == "node"
    assert config.language == "javascript" and config.test_runner == "node" and config.python == ""
    assert config.protected == JS_PROTECTED and detection.test_count == 2
    assert js_config(detect_js(tmp_path, which=which_node)).test_runner == "node"
    text = detection.describe()
    assert "Node.js:" in text and "2 tests in 1 files" in text and "node" in text


def test_the_first_run_reports_a_missing_runner(tmp_path: Path) -> None:
    write(tmp_path, **{"a.test.ts": ""})
    package(tmp_path, devDependencies={"vitest": "1"})
    first = first_run(tmp_path, which=lambda name: NODE if name == "node" else None)
    assert first.detection is not None and first.detection.language == "javascript"
    assert any("JavaScript test runner is not available" in p for p in first.problems)
    assert not any("pytest" in p for p in first.problems)


def test_counting_js_tests(tmp_path: Path) -> None:
    write(
        tmp_path,
        **{
            "a.test.js": "describe('s', () => {\n  it('a', () => {});\n  test.skip('b', () => {});\n});\n"
        },
    )
    assert count_js_tests(tmp_path, ["a.test.js", "missing.test.js"]) == 2


def test_a_python_project_is_still_detected_as_python(tmp_path: Path) -> None:
    write(tmp_path, **{"tests__test_a.py": "def test_a():\n    pass\n"})
    detection = detect_project(tmp_path)
    assert detection.language == "python" and detection.runner == "pytest"
    assert detection.config.language == "python" and detection.config.test_runner == "pytest"


# ------------------------------------------------------------------ guards


def js_project() -> ProjectConfig:
    return js_config(detect_js(Path("/nonexistent-qed"), which=which_node, probe=node_probe()))


@pytest.mark.parametrize(
    "line",
    ["it.skip('x', () => {});", "test.todo('x');", "describe.only('s', () => {});", "xit('x', () => {});",
     "fit('x', () => {});", "t.skip();", "test('x', { skip: true }, () => {});"],
)  # fmt: skip
def test_added_skips_and_only_in_a_js_test_are_rejected(line: str) -> None:
    diff = diff_adding("src/a.test.js", [line])
    check = scope_check(
        diff, ProjectConfig(), "coder", Speed.NORMAL, allowed_added=["src/a.test.js"]
    )
    assert not check.passed and "adds a skip" in check.details


def test_removed_expectations_weaken_a_js_test() -> None:
    diff = diff_adding(
        "src/a.test.ts",
        ["it('a', () => {});"],
        removed=["  expect(add(1, 2)).toBe(3);", "  assert.equal(1, 1);"],
    )
    check = scope_check(diff, js_project(), "coder", Speed.NORMAL, allow_protected=True)
    assert not check.passed and "removes 2 assert line(s)" in check.details
    kept = diff_adding(
        "src/a.test.ts", ["  expect(add(1, 2)).toBe(3);"], removed=["  expect(add(1, 2)).toBe(2);"]
    )
    assert scope_check(kept, js_project(), "coder", Speed.NORMAL, allow_protected=True).passed


def test_a_new_js_test_is_allowed_and_comments_do_not_count() -> None:
    diff = diff_adding(
        "src/new.test.js", ["// it.skip('old', () => {});", "it('x', () => {});"], new=True
    )
    check = scope_check(diff, js_project(), "test_writer", Speed.NORMAL)
    assert check.passed


def test_changing_an_existing_js_test_or_package_json_is_protected() -> None:
    for path in ("src/a.test.js", "package.json"):
        check = scope_check(diff_adding(path, ["x"]), js_project(), "coder", Speed.NORMAL)
        assert not check.passed and "protected file" in check.summary
    assert scope_check(
        diff_adding("src/math.ts", ["export const x = 1;"]), js_project(), "coder", Speed.NORMAL
    ).passed


@pytest.mark.parametrize(
    ("line", "name"),
    [
        ("const cp = require('child_process');", "child_process"),
        ("import { execSync } from 'node:child_process';", "child_process"),
        ("execSync('rm -rf /');", "execSync/spawnSync"),
        ("const r = eval(code);", "eval("),
        ("const f = new Function('a', 'return a');", "new Function("),
        ("await fs.promises.rm(dir, { recursive: true });", "fs delete"),
        ("fs.unlinkSync(p);", "fs delete"),
        ("const r = await fetch(url);", "fetch("),
        ("import axios from 'axios';", "axios"),
        ("import https from 'node:https';", "network module"),
        ("process.exit(1);", "process.exit("),
        ("const home = os.homedir();", "os.homedir("),
    ],
)  # fmt: skip
def test_risky_javascript_is_found(line: str, name: str) -> None:
    findings = risk_findings(diff_adding("src/a.ts", [line]))
    assert name in [f.pattern for f in findings]
    check = risk_check(diff_adding("src/a.ts", [line]))
    assert check.needs_approval and not check.passed


def test_javascript_comments_and_safe_code_are_not_risky() -> None:
    safe = [
        "// fetch(url) would be risky",
        " * execSync('x')",
        "const x = a.fetch;",
        "export const y = 1;",
    ]
    assert risk_findings(diff_adding("src/a.js", safe)) == []
    assert risk_check(diff_adding("src/a.js", safe)).passed


def test_the_two_languages_use_their_own_patterns() -> None:
    assert risk_findings(diff_adding("src/a.js", ["import os", "subprocess.run(x)"])) == []
    assert risk_findings(diff_adding("a.py", ["fetch(url)", "eval(x)"]))[0].pattern == "eval("
    assert [f.pattern for f in risk_findings(diff_adding("a.py", ["fetch(url)"]))] == []


# ------------------------------------------------------------------ parse stage


def test_the_parse_stage_checks_js_and_ts_files(tmp_path: Path) -> None:
    write(
        tmp_path,
        **{
            "good.ts": "export const a: number = 1;\n",
            "bad.js": "function f( {\n",
            "ok.py": "x = 1\n",
        },
    )
    good = parse_check(tmp_path, ["good.ts", "ok.py"])
    assert good.passed and "2 changed file(s) parse" in good.summary
    bad = parse_check(tmp_path, ["good.ts", "bad.js"])
    assert not bad.passed and "bad.js, line 1" in bad.summary
    assert parse_check(tmp_path, ["README.md"]).summary == "No source files changed."
    assert parse_check(tmp_path, ["gone.ts"]).passed


# ------------------------------------------------------------------ the map and related tests


SRC_TS = "export function add(a: number, b: number) { return a - b; }\nexport class Cart { total() { return 1; } }\n"


def sample_ts_project(root: Path) -> Path:
    write(
        root,
        **{
            "src__math.ts": SRC_TS,
            "src__math.test.ts": "import { add } from './math.js';\nit('adds', () => {});\n",
            "src__cart.ts": "export const total = () => 1;\n",
            "src__util__index.ts": "export const u = 1;\n",
            "src__util.test.ts": "import { u } from './util';\n",
            "src__other.test.ts": "import { x } from './nothing';\nit('o', () => {});\n",
            "tsconfig.json": "{}",
            "node_modules__lib__skip.js": "x",
        },
    )
    package(root)
    return root


def test_source_files_by_language(tmp_path: Path) -> None:
    write(
        tmp_path,
        **{
            "a.py": "",
            "b.ts": "",
            "c.d.ts": "",
            "d.min.js": "",
            "e.jsx": "",
            "node_modules__x.js": "",
        },
    )
    assert list_source_files(tmp_path, "python") == ["a.py"]
    assert list_source_files(tmp_path, "javascript") == ["b.ts", "e.jsx"]


def test_the_repo_map_lists_js_symbols(tmp_path: Path) -> None:
    root = sample_ts_project(tmp_path)
    assert top_level_symbols_js(SRC_TS, "ts") == ["add", "Cart[total]"]
    text = repo_map(root, 2000, language="javascript")
    assert "src/math.ts: add, Cart[total]" in text and "node_modules" not in text
    assert "src/cart.ts: total" in text


def test_related_tests_by_name_and_by_import(tmp_path: Path) -> None:
    root = sample_ts_project(tmp_path)
    assert related_tests(["src/math.ts"], root, "javascript") == ["src/math.test.ts"]
    assert related_tests(["src/util/index.ts"], root, "javascript") == ["src/util.test.ts"]
    assert related_tests(["src/cart.ts"], root, "javascript") == []
    assert related_tests(["src/other.test.ts"], root, "javascript") == ["src/other.test.ts"]
    assert related_tests(["README.md"], root, "javascript") == []
    # the Python behaviour is unchanged
    assert related_tests(["src/math.ts"], root) == []


def test_resolve_js_import_handles_extensions_and_index_files() -> None:
    sources = {"src/math.ts", "src/util/index.ts", "src/x.js"}
    assert resolve_js_import("src/math.test.ts", "./math.js", sources) == "src/math.ts"
    assert resolve_js_import("src/math.test.ts", "./math", sources) == "src/math.ts"
    assert resolve_js_import("src/a/b.test.ts", "../util", sources) == "src/util/index.ts"
    assert resolve_js_import("src/a.test.ts", "./x.js", sources) == "src/x.js"
    assert resolve_js_import("src/a.test.ts", "./nope", sources) is None


def test_select_targeted_for_javascript(tmp_path: Path) -> None:
    root = sample_ts_project(tmp_path)
    assert select_targeted(["src/math.ts"], root, language="javascript") == ["src/math.test.ts"]
    assert (
        select_targeted(["src/cart.ts"], root, language="javascript", baseline_full_seconds=5)
        is None
    )
    assert select_targeted(["src/cart.ts"], root, language="javascript") == []
    assert select_targeted(
        ["src/math.ts"], root, repro_files=["src/new.test.ts"], language="javascript"
    ) == ["src/new.test.ts", "src/math.test.ts"]


def test_repro_test_paths_for_javascript(tmp_path: Path) -> None:
    root = sample_ts_project(tmp_path)
    tests = find_test_files(root, "javascript")
    path = repro_test_path("Cart total ignores discounts today", tests, root, "javascript")
    assert path == "src/qed_cart_total_ignores_discounts_today.test.ts"
    (root / path).write_text("")
    assert repro_test_path(
        "Cart total ignores discounts today", tests, root, "javascript"
    ).endswith("_2.test.ts")
    plain = tmp_path / "plain"
    write(plain, **{"a.js": ""})
    package(plain)
    assert repro_test_path("fix it", [], plain, "javascript") == "tests/qed_fix_it.test.js"
    assert js_test_extension(["t/a.test.mjs", "t/b.test.mjs", "t/c.test.js"], plain) == "mjs"
    assert js_test_extension(["t/a.test.tsx"], plain) == "ts"
    assert repro_test_path("fix it", [], plain) == "tests/test_qed_fix_it.py"  # Python unchanged


def test_build_context_for_a_javascript_project(tmp_path: Path) -> None:
    root = sample_ts_project(tmp_path)
    spec = TaskSpec(id="t-1", request="fix add() in math.ts", speed=Speed.NORMAL)
    seen: dict[str, str] = {}

    def locate(map_text: str, hits: str) -> list[str]:
        seen["map"], seen["hits"] = map_text, hits
        return ["src/math.ts"]

    bundle = build_context(
        root, spec, locate, max_files=3, context_tokens=8192, language="javascript"
    )
    assert bundle.located == ["src/math.ts"] and "src/math.ts: add, Cart[total]" in seen["map"]
    assert bundle.related_tests == ["src/math.test.ts"]
    assert bundle.example_test is not None and bundle.example_test.path == "src/math.test.ts"
    assert "src/math.test.ts" in seen["hits"]  # the search saw the JS files


def test_the_detected_config_round_trips_through_project_toml(tmp_path: Path) -> None:
    from qedcode.config import load_project, save_project

    config = js_config(detect_js(tmp_path, which=which_node, probe=node_probe()))
    save_project(tmp_path, config)
    loaded = load_project(tmp_path)
    assert loaded.language == "javascript" and loaded.test_runner == "node"
    assert loaded.protected == JS_PROTECTED
    assert ProjectDetection(root=tmp_path, python="", python_source="").language == "python"


@pytest.mark.parametrize(
    "path",
    [
        ".gitignore",
        "sub/.gitattributes",
        ".editorconfig",
        "LICENSE",
        "LICENSE.txt",
        "Licence.md",
        "README.md",
        "CHANGELOG.rst",
        "docs/guide.txt",
        "docs/img/logo.png",
    ],
)
def test_inert_paths(path: str) -> None:
    assert is_inert_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "pyproject.toml",
        "setup.cfg",
        "requirements.txt",
        "src/app/data.json",
        "config.yaml",
        "templates/page.html",
        "docs/conf.py",
        "docs/example.ts",
        "src/app/main.py",
    ],
)
def test_paths_that_can_change_what_tests_do_are_not_inert(path: str) -> None:
    assert not is_inert_path(path)


def test_docs_are_not_inert_when_pytest_runs_them() -> None:
    assert not is_inert_path("README.md", docs_are_tested=True)
    assert not is_inert_path("docs/guide.txt", docs_are_tested=True)
    assert is_inert_path(".gitignore", docs_are_tested=True)
