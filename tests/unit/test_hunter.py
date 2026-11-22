"""Bug Hunter (AGENTS.md 15.12): findings need a failing test, and end with a proven fix or not."""

from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from qedcode import tour
from qedcode.cli import app
from qedcode.engine import api
from qedcode.engine.hunter import docstrings, hunt_test_path
from qedcode.engine.safety_net import Target
from qedcode.models.schemas import Speed
from qedcode.runtime.fake import FakeModel
from qedcode.vcs.git import git

SUSPECTS = '''EDIT create_file path=tests/x.py
```python
from csvlite.parser import parse_csv_line


def test_empty_line_crashes():
    """An empty line raises IndexError instead of giving an empty list."""
    assert parse_csv_line("") == []


def test_plain_line_is_fine():
    """A plain line might be split wrongly."""
    assert parse_csv_line("a,b") == ["a", "b"]
```
'''
NOTHING_WRONG = '''EDIT create_file path=tests/y.py
```python
from csvlite.parser import parse_csv_rows


def test_rows_are_fine():
    """Blank lines might be kept."""
    assert parse_csv_rows("a\\n\\nb") == [["a"], ["b"]]
```
'''


def _script() -> dict[str, list[str]]:
    script = yaml.safe_load((tour.TOUR_DIR / "004_empty_input_crash" / "script.yaml").read_text())
    script.pop("test_writer")  # the hunter's failing test is the repro
    script["coder"] = script["coder"][1:]  # no cheating try here
    script["hunter"] = [SUSPECTS, NOTHING_WRONG]
    return script


def test_docstrings_and_paths() -> None:
    assert docstrings(SUSPECTS.split("```python\n")[1].split("```")[0]) == {
        "test_empty_line_crashes": "An empty line raises IndexError instead of giving an empty list.",
        "test_plain_line_is_fine": "A plain line might be split wrongly.",
    }
    target = Target("src/csvlite/parser.py", "parse_csv_line", 4, 9)
    assert hunt_test_path(target) == "tests/test_qed_hunt_parser_parse_csv_line.py"


def test_a_confirmed_bug_gets_a_proven_fix(tmp_path: Path) -> None:
    repo = tour.prepare("004_empty_input_crash", tmp_path)
    model = FakeModel(_script())
    result = api.hunt(repo, speed=Speed.NORMAL, client=model)
    assert result.status == "done", result.message
    (finding,) = result.findings  # the passing suspicions were dropped
    assert finding.test_name == "test_empty_line_crashes"
    assert finding.description.startswith("An empty line raises IndexError")
    assert "test_plain_line_is_fine" not in finding.test_code
    assert finding.fixed and finding.result is not None and finding.result.receipt_path
    assert "wrong expectation" in result.message
    assert git(repo, "status", "--porcelain").stdout.strip() == ""  # nothing applied


def test_no_failing_test_means_no_finding(tmp_path: Path, monkeypatch) -> None:
    repo = tour.prepare("007_sort_order", tmp_path)
    passing = '''EDIT create_file path=tests/z.py
```python
from board.scores import best_score


def test_best():
    """Might return the wrong one."""
    assert best_score([1, 3]) == 3
```
'''
    model = FakeModel({"hunter": [passing, passing]})
    result = api.hunt(repo, speed=Speed.NORMAL, client=model)
    assert result.status == "nothing" and not result.findings
    monkeypatch.chdir(repo)
    monkeypatch.setattr(
        "qedcode.engine.api.make_client", lambda cfg, role: FakeModel({"hunter": [passing] * 3})
    )
    shown = CliRunner().invoke(app, ["hunt"])
    assert shown.exit_code == 0 and "No suspected bug" in shown.output
