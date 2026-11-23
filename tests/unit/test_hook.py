"""`qed hook` (AGENTS.md 15.18): the agent may stop only when the check has no objection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qedcode import hook
from qedcode.cli import app
from qedcode.engine.check import CheckOutcome
from tests.unit.test_tamper import CHEATS, HONEST, noise, swap, write  # noqa: F401
from tests.unit.test_tamper import shop as shop  # the fixture

runner = CliRunner()
BREAK = swap("src/shop/pages.py", "return 0", "return 5")


def event(project: Path, **extra) -> str:
    return json.dumps({"hook_event_name": "Stop", "cwd": str(project), **extra})


def test_install_keeps_other_settings_and_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir()
    other = {"hooks": [{"type": "command", "command": "say done"}]}
    path.write_text(json.dumps({"model": "x", "hooks": {"Stop": [other], "PreToolUse": []}}))
    assert hook.install(tmp_path).changed
    assert not hook.install(tmp_path).changed
    data = json.loads(path.read_text())
    assert data["model"] == "x" and data["hooks"]["PreToolUse"] == []
    assert data["hooks"]["Stop"] == [other, hook.ENTRY]
    assert str(tmp_path) not in path.read_text()  # no path of this computer in a shared file
    assert hook.remove(tmp_path).changed and not hook.remove(tmp_path).changed
    assert json.loads(path.read_text())["hooks"]["Stop"] == [other]


def test_removing_the_only_hook_leaves_no_empty_shell(tmp_path: Path) -> None:
    hook.install(tmp_path)
    hook.remove(tmp_path)
    assert json.loads((tmp_path / ".claude" / "settings.json").read_text()) == {}


@pytest.mark.parametrize("text", ["{ not json", '{"hooks": []}', '{"hooks": {"Stop": {}}}'])
def test_settings_it_does_not_understand_are_left_alone(tmp_path: Path, text: str) -> None:
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir()
    path.write_text(text)
    with pytest.raises(hook.HookError, match="left alone"):
        hook.install(tmp_path)
    assert path.read_text() == text


def test_a_cheat_keeps_the_agent_working(shop: Path) -> None:
    noise(shop)
    CHEATS["skip_decorator"](shop)
    code, reason = hook.run_claude(event(shop), shop)
    assert code == 2 and "Caught" in reason and "Put those tests back" in reason


def test_a_broken_test_keeps_the_agent_working(shop: Path) -> None:
    BREAK(shop)
    code, reason = hook.run_claude(event(shop), shop)
    assert code == 2 and "fail now" in reason and "test_table" in reason


def test_honest_work_no_work_and_a_second_stop_are_let_through(shop: Path, tmp_path: Path) -> None:
    assert hook.run_claude(event(shop), shop) == (0, "")  # nothing changed
    HONEST["fix_and_new_test_file"](shop)
    assert hook.run_claude(event(shop), shop) == (0, "")
    CHEATS["skip_decorator"](shop)
    assert hook.run_claude(event(shop, stop_hook_active=True), shop) == (0, "")
    assert hook.run_claude("not json", tmp_path) == (0, "")  # no repository here


def test_a_crash_in_qedcode_never_blocks_the_agent(shop: Path, monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise RuntimeError("inside")

    monkeypatch.setattr("qedcode.hook.api.check", boom)
    assert hook.run_claude(event(shop), shop) == (0, "")


def test_a_rejection_that_is_not_about_tests_does_not_block() -> None:
    assert hook.verdict(CheckOutcome(status="rejected", message="lint")) == ""


def test_the_commands(shop: Path, monkeypatch) -> None:
    monkeypatch.chdir(shop)
    assert runner.invoke(app, ["hook", "install", "cursor"]).exit_code == 2
    assert runner.invoke(app, ["hook", "install", "claude"]).exit_code == 0
    noise(shop)
    CHEATS["assert_true"](shop)
    result = runner.invoke(app, ["hook", "run", "claude"], input=event(shop))
    assert result.exit_code == 2 and "Caught" in result.output
    assert runner.invoke(app, ["hook", "remove"]).exit_code == 0
