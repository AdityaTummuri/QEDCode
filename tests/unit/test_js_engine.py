"""End to end on a JavaScript project: real git, real `node --test`, a scripted FakeModel."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from qedcode.config import GlobalConfig, ProjectConfig, load_project, save_project
from qedcode.engine import api
from qedcode.engine.roles import PYTHON_STACK, stack_for
from qedcode.models.schemas import Grade, Mode, Speed
from qedcode.project.detect import first_run
from qedcode.runtime.fake import FakeModel
from qedcode.verify.receipt import NETWORK_NOTE, load_receipt
from tests.conftest import run_git

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")

PAGES = """export function paginate(items, page, size) {
  const start = (page - 1) * size;
  const end = start + size - 1;
  return items.slice(start, end);
}
"""
EXISTING_TEST = """import test from 'node:test';
import assert from 'node:assert/strict';
import { paginate } from './pages.js';

test('an empty list stays empty', () => {
  assert.deepEqual(paginate([], 1, 10), []);
});
"""
REPRO = """import test from 'node:test';
import assert from 'node:assert/strict';
import { paginate } from './pages.js';

test('a full page has page-size items', () => {
  assert.deepEqual(paginate([1, 2, 3, 4, 5], 1, 2), [1, 2]);
});

test('the last partial page keeps its last item', () => {
  assert.deepEqual(paginate([1, 2, 3, 4, 5], 3, 2), [5]);
});
"""
FIX = """PLAN:
- the end index must be start + size

EDIT replace_symbol path=src/pages.js symbol=paginate
```javascript
function paginate(items, page, size) {
  const start = (page - 1) * size;
  const end = start + size;
  return items.slice(start, end);
}
```
"""
SCRIPT = {
    "intake": [
        "KIND: bugfix\nACCEPTANCE:\n- every page holds exactly `size` items\nQUESTION: NONE\n"
    ],
    "locator": ["FILE: src/pages.js\n"],
    "test_writer": [
        "EDIT create_file path=src/qed_paginate_drops_the_last_item.test.js\n"
        f"```javascript\n{REPRO}```\nFAILS NOW BECAUSE: every page loses its last item\n"
    ],
    "coder": [FIX],
    "explainer": ["paginate cut each page one item short. The end index is now start + size."],
}


def js_repo(tmp_path: Path, *, confirmed: bool = True) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "package.json").write_text(
        '{"name":"shop","type":"module","scripts":{"test":"node --test"}}'
    )
    (root / "src" / "pages.js").write_text(PAGES)
    (root / "src" / "pages.test.js").write_text(EXISTING_TEST)
    (root / ".gitignore").write_text("node_modules/\n")
    run_git(root, "init", "-q", "-b", "main")
    run_git(root, "add", "-A")
    run_git(root, "commit", "-q", "-m", "buggy")
    if confirmed:
        first = first_run(root)
        assert first.detection is not None and first.detection.language == "javascript"
        save_project(root, first.config.model_copy(update={"confirmed": True}))
    return root


def solve(root: Path, script: dict | None = None, **kwargs):
    model = FakeModel(script or SCRIPT)
    result = api.solve(
        root, "paginate drops the last item", config=GlobalConfig(), client=model, **kwargs
    )
    return result, model


def test_first_run_detects_a_node_project(tmp_path: Path) -> None:
    root = js_repo(tmp_path, confirmed=False)
    first = first_run(root)
    assert first.detection is not None
    assert first.detection.language == "javascript" and first.detection.runner == "node"
    assert first.config.python == "" and first.config.test_runner == "node"
    saved = js_repo(tmp_path / "again")
    assert load_project(saved).language == "javascript"


def test_a_javascript_bug_is_fixed_to_grade_a_with_real_node(tmp_path: Path) -> None:
    root = js_repo(tmp_path)
    result, model = solve(root)
    assert result.status == "done", result.message
    assert result.best is not None and result.best.grade is Grade.A
    assert result.best.id == "c1" and result.best.full_suite_ran
    stages = {c.stage: c.passed for c in result.best.checks}
    assert stages["parse"] and stages["fbpa"] and stages["targeted"] and stages["full"]
    assert "lint" not in stages  # no linter for JavaScript yet: the stage is skipped, not faked
    diff = result.best.diff
    assert (
        "const end = start + size;" in diff and "qed_paginate_drops_the_last_item.test.js" in diff
    )
    assert "node_modules" not in diff  # the test-time link never reaches the candidate
    receipt = load_receipt(result.receipt_path)
    assert receipt.fbpa_tests and all(k.startswith("src/qed_paginate") for k in receipt.fbpa_tests)
    assert receipt.tests_added == ["src/qed_paginate_drops_the_last_item.test.js"]
    assert receipt.commands and all(c[0] == "{node}" for c in receipt.commands)
    assert NETWORK_NOTE in receipt.unverified and receipt.sandbox == ""
    # the prompts used the JavaScript wording, not pytest
    prompts = [c.messages[-1]["content"] for c in model.calls]
    writer = next(p for c, p in zip(model.calls, prompts, strict=True) if c.tag == "test_writer")
    assert "node:test test" in writer and "```javascript" in writer and "pytest" not in writer
    coder = next(p for c, p in zip(model.calls, prompts, strict=True) if c.tag == "coder")
    assert "```javascript" in coder and "export function" not in coder.split("CODE")[0]


def test_the_users_tree_is_untouched_until_apply_and_apply_works(tmp_path: Path) -> None:
    root = js_repo(tmp_path)
    result, _ = solve(root)
    assert (root / "src" / "pages.js").read_text() == PAGES  # solve changed nothing
    assert (
        not (root / "node_modules").exists()
        and not (root / "src" / "qed_paginate_drops_the_last_item.test.js").exists()
    )
    outcome = api.apply_result(root, result.task.id)
    assert outcome.status == "applied" and outcome.grade is Grade.A
    fixed = (root / "src" / "pages.js").read_text()
    assert "const end = start + size;" in fixed and fixed.startswith("export function paginate")
    assert (root / "src" / "qed_paginate_drops_the_last_item.test.js").exists()
    assert api.undo(root).status == "undone" and (root / "src" / "pages.js").read_text() == PAGES


def test_verify_reruns_the_node_receipt_and_catches_a_changed_patch(tmp_path: Path) -> None:
    root = js_repo(tmp_path)
    result, _ = solve(root)
    verified = api.verify(result.receipt_path, root)
    assert verified.verified, verified.mismatches
    import json

    path = Path(result.receipt_path)
    data = json.loads(path.read_text())
    data["patch"] = data["patch"].replace("start + size;", "start + size + 1;")
    path.write_text(json.dumps(data))
    broken = api.verify(path, root)
    assert not broken.verified and "checksum" in broken.mismatches[0]


def test_verify_works_in_a_folder_whose_project_file_is_missing(tmp_path: Path) -> None:
    root = js_repo(tmp_path)
    result, _ = solve(root)
    (root / ".qed" / "project.toml").unlink()  # a fresh clone: nothing confirmed yet
    assert api.verify(result.receipt_path, root).verified


def test_a_candidate_that_skips_a_test_is_rejected_like_in_python(tmp_path: Path) -> None:
    root = js_repo(tmp_path)
    tamper = (
        "PLAN:\n- skip the failing test\n\nEDIT search_replace path=src/pages.test.js\n"
        "<<<<<<< SEARCH\ntest('an empty list stays empty', () => {\n=======\n"
        "test.skip('an empty list stays empty', () => {\n>>>>>>> REPLACE\n"
    )
    script = {**SCRIPT, "coder": [tamper, FIX, FIX]}
    result, _ = solve(root, script)
    assert result.status == "done" and result.best is not None and result.best.id != "c1"
    first = next(c for c in result.candidates if c.id == "c1")
    assert first.grade is None and first.failed_stage == "scope"
    assert "protected" in first.checks[-1].summary or "skip" in first.checks[-1].summary


def test_a_candidate_with_risky_javascript_needs_approval_and_is_capped_at_b(
    tmp_path: Path,
) -> None:
    root = js_repo(tmp_path)
    risky = FIX.replace("const end = start + size;", "const end = start + size;\n  eval('1');")
    result, _ = solve(root, {**SCRIPT, "coder": [risky]}, allow_risky=True)
    assert result.status == "done" and result.best is not None
    assert result.best.grade is Grade.B
    assert any("eval(" in g for g in load_receipt(result.receipt_path).unverified)


def test_a_syntax_error_in_new_javascript_is_caught_at_the_applier(tmp_path: Path) -> None:
    root = js_repo(tmp_path)
    broken = FIX.replace("return items.slice(start, end);", "return items.slice(start, end")
    result, _ = solve(root, {**SCRIPT, "coder": [broken, FIX, FIX]})
    assert result.status == "done"
    first = next(c for c in result.candidates if c.id == "c1")
    assert first.grade is None and first.failed_stage == "apply"
    assert "does not parse" in first.checks[-1].summary or "syntax" in first.checks[-1].summary


def test_fast_speed_on_javascript_reaches_grade_b(tmp_path: Path) -> None:
    root = js_repo(tmp_path)
    result, model = solve(root, speed=Speed.FAST)
    assert result.status == "done" and result.best is not None and result.best.grade is Grade.B
    assert not any(c.tag == "test_writer" for c in model.calls)


def test_qed_check_grades_hand_written_javascript_changes(tmp_path: Path) -> None:
    root = js_repo(tmp_path)
    (root / "src" / "pages.js").write_text(PAGES.replace("size - 1;", "size;"))
    outcome = api.check(root, speed=Speed.NORMAL)
    assert outcome.status == "graded", outcome.message
    assert outcome.grade in (Grade.B, Grade.C), outcome.message
    assert not (root / "node_modules").exists()


def test_a_broken_baseline_is_reported_not_hidden(tmp_path: Path) -> None:
    root = js_repo(tmp_path)
    (root / "src" / "pages.test.js").write_text("import { nope } from './missing.js';\n")
    run_git(root, "add", "-A")
    run_git(root, "commit", "-q", "-m", "broken test file")
    result, _ = solve(root, {**SCRIPT, "test_writer": SCRIPT["test_writer"] * 2})
    assert result.status == "done"
    assert "pages.test.js" in " ".join(load_receipt(result.receipt_path).known_failures)


def test_python_and_javascript_wording_stay_separate() -> None:
    assert stack_for(None) is PYTHON_STACK and stack_for(ProjectConfig()) is PYTHON_STACK
    ts = stack_for(ProjectConfig(language="javascript", test_runner="vitest"), "src/a.test.ts")
    assert ts.fence == "typescript" and "Vitest" in ts.project and "vitest" in ts.test_rules
    assert stack_for(ProjectConfig(language="javascript", test_runner="jest")).fence == "javascript"
    assert "Jest" in stack_for(ProjectConfig(language="javascript", test_runner="jest")).project
    assert Mode.LOCAL and Speed.NORMAL


# ------------------------------------------------------------------ doctor and the sealed box


def test_the_doctor_checks_node_and_the_runner_for_a_js_project(tmp_path: Path) -> None:
    from qedcode.engine.doctor import GlobalConfig as _Config  # noqa: F401  (import check)
    from qedcode.engine.doctor import check_javascript, project_language, run_doctor
    from qedcode.hardware.detect import HardwareInfo

    root = js_repo(tmp_path)
    assert project_language(root) == "javascript"
    node, runner = check_javascript(root)
    assert node.name == "node" and node.status == "ok" and "Node.js" in node.detail
    assert runner.name == "test runner" and runner.status == "ok" and "node:test" in runner.detail
    hw = HardwareInfo(vendor="cpu", backend="cpu", source="fallback")
    names = [c.name for c in run_doctor(root, GlobalConfig(), hw=hw)]
    assert "node" in names and "test runner" in names
    assert "python" not in names and "pytest" not in names


def test_the_doctor_explains_a_missing_vitest(tmp_path: Path) -> None:
    from qedcode.engine.doctor import check_javascript

    root = js_repo(tmp_path)
    (root / "package.json").write_text(
        '{"name":"shop","devDependencies":{"vitest":"^3"},"packageManager":"pnpm@9.0.0"}'
    )
    _node, runner = check_javascript(root)
    assert runner.status == "fail" and "not installed in node_modules" in runner.detail
    assert "pnpm install" in runner.fix and "never installs Node packages" in runner.fix


def test_the_sealed_test_box_is_refused_for_a_javascript_project(tmp_path: Path) -> None:
    from qedcode.engine import sandbox_setup

    root = js_repo(tmp_path)
    asked: list[str] = []
    result = sandbox_setup.setup(root, lambda prompt: asked.append(prompt) or True)
    assert not result.ok and "Python projects only" in result.message and not asked
