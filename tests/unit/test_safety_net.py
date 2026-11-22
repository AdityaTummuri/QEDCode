"""Safety Net Builder (AGENTS.md 15.11): keep tests that pass today AND catch mutants."""

from __future__ import annotations

import sys
from pathlib import Path

from typer.testing import CliRunner

from qedcode.cli import app
from qedcode.config import ProjectConfig, save_project
from qedcode.engine import api
from qedcode.engine.safety_net import find_untested, keep_only, net_test_path
from qedcode.models.schemas import Grade, Speed
from qedcode.runtime.fake import FakeModel
from qedcode.vcs.git import git

SOURCE = '''"""Small helpers."""


def clamp(x, lo, hi):
    """Keep x between lo and hi."""
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def double(x):
    return x * 2


def _private(x):
    return x
'''
TESTS = "from mathy.ops import double\n\n\ndef test_double():\n    assert double(2) == 4\n"
NET_REPLY = """EDIT create_file path=tests/whatever.py
```python
from mathy.ops import clamp


def test_inside_stays():
    assert clamp(5, 0, 10) == 5


def test_below_and_above_are_clamped():
    assert clamp(-3, 0, 10) == 0
    assert clamp(42, 0, 10) == 10


def test_padding_checks_nothing():
    assert callable(clamp)


def test_wrong_expectation():
    assert clamp(-1, 0, 10) == -1
```
"""


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "src" / "mathy").mkdir(parents=True)
    (root / "src" / "mathy" / "__init__.py").write_text("")
    (root / "src" / "mathy" / "ops.py").write_text(SOURCE)
    (root / "tests").mkdir()
    (root / "tests" / "test_ops.py").write_text(TESTS)
    (root / "pyproject.toml").write_text('[tool.pytest.ini_options]\npythonpath = ["src"]\n')
    (root / ".gitignore").write_text("__pycache__/\n.qed/\n")
    for args in (("init", "-q"), ("add", "-A"), ("commit", "-q", "-m", "init")):
        git(root, *args, identity=True)
    save_project(root, ProjectConfig(python=sys.executable, confirmed=True))
    return root


def test_untested_public_functions_are_found(tmp_path: Path) -> None:
    root = _project(tmp_path)
    targets = find_untested(root)
    assert [(t.path, t.symbol) for t in targets] == [("src/mathy/ops.py", "clamp")]
    assert targets[0].start == 4 and targets[0].end == 10
    assert find_untested(root, "src/other") == []
    assert net_test_path(targets[0]) == "tests/test_qed_net_ops_clamp.py"


def test_keep_only_removes_other_tests() -> None:
    code = "import x\n\n\ndef test_a():\n    pass\n\n\ndef test_b():\n    pass\n\n\ndef helper():\n    pass\n"
    kept = keep_only(code, {"test_b"})
    assert "def test_b" in kept and "def test_a" not in kept and "def helper" in kept


def test_the_net_keeps_only_tests_that_catch_mutants(tmp_path: Path) -> None:
    root = _project(tmp_path)
    model = FakeModel({"safety_net": [NET_REPLY]})
    result = api.build_tests(root, speed=Speed.NORMAL, client=model)
    assert result.status == "done", result.message
    diff = result.best.diff
    assert "tests/test_qed_net_ops_clamp.py" in diff
    assert "test_below_and_above_are_clamped" in diff
    assert "test_padding_checks_nothing" not in diff  # passes today, catches no mutant
    assert "test_wrong_expectation" not in diff  # fails on today's code
    assert result.best.grade is Grade.C  # tests only: no behaviour changed, by design
    receipt = result.receipt_path
    assert receipt and Path(receipt).is_file()
    assert "catch" in result.message and "qed apply" in result.message
    assert not list((root / ".qed" / "worktrees").glob("*"))
    assert git(root, "status", "--porcelain").stdout.strip() == ""  # the user's tree is untouched


def test_nothing_untested_and_cli(tmp_path: Path, monkeypatch) -> None:
    root = _project(tmp_path)
    (root / "tests" / "test_more.py").write_text("from mathy.ops import clamp\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "more", identity=True)
    result = api.build_tests(root, client=FakeModel({}))
    assert result.status == "no_fix" and "already mentioned" in result.message
    monkeypatch.chdir(root)
    monkeypatch.setattr("qedcode.engine.api.make_client", lambda cfg, role: FakeModel({}))
    shown = CliRunner().invoke(app, ["tests"])
    assert shown.exit_code == 1 and "already mentioned" in shown.output
