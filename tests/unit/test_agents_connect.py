"""One-click agent connect (AGENTS.md section 15.4). Never touches the real home or PATH."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qedcode import agents_connect as ac
from qedcode.cli import app
from qedcode.verify.run import RunResult

QED = "/opt/tools/bin/qed"


class Spy:
    def __init__(self, ok: bool = True, output: str = "") -> None:
        self.commands: list[list[str]] = []
        self.ok, self.output = ok, output

    def __call__(self, args: list[str]) -> RunResult:
        self.commands.append(args)
        if self.ok:
            return RunResult(args=args, exit_code=0)
        return RunResult(args=args, exit_code=1, stderr=self.output or "boom")


def make_env(
    tmp_path: Path, *, have: tuple[str, ...] = ("qed",), spy: Spy | None = None, **env
) -> ac.Environment:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    paths = {"qed": QED, "claude": "/usr/bin/claude", "opencode": "/usr/bin/opencode"}
    return ac.Environment(
        home=home,
        which=lambda name: paths[name] if name in have and name in paths else None,
        run_command=spy or Spy(),
        env=env,
        clock=lambda: 1_700_000_000.0,
    )


def yes(prompt: str) -> bool:
    yes.prompts.append(prompt)  # type: ignore[attr-defined]
    return True


yes.prompts = []  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _reset_prompts():
    yes.prompts.clear()  # type: ignore[attr-defined]


def no(prompt: str) -> bool:
    return False


def never(prompt: str) -> bool:
    raise AssertionError(f"nothing should have been asked: {prompt}")


def cursor_file(env: ac.Environment) -> Path:
    return ac.cursor_settings_path(env)


# ------------------------------------------------------------------ detection


def test_nothing_is_found_on_an_empty_computer(tmp_path: Path) -> None:
    found = ac.detect_agents(make_env(tmp_path))
    assert [s.key for s in found] == ["claude", "opencode", "cline", "cursor"]
    assert not any(s.detected or s.connected for s in found)


def test_agents_are_detected_by_their_command_or_settings_folder(tmp_path: Path) -> None:
    env = make_env(tmp_path, have=("qed", "claude", "opencode"))
    (env.home / ".cursor").mkdir()
    (env.home / ".cline" / "data" / "settings").mkdir(parents=True)
    by_key = {s.key: s for s in ac.detect_agents(env)}
    assert all(by_key[k].detected for k in ac.AGENTS)
    assert by_key["claude"].method == "command" and by_key["cursor"].method == "file"
    assert by_key["claude"].target == f"claude mcp add qedcode -- {QED} mcp"
    assert by_key["opencode"].target == f"opencode mcp add qedcode -- {QED} mcp"
    assert by_key["cursor"].target == str(env.home / ".cursor" / "mcp.json")
    assert by_key["cline"].target.endswith("cline_mcp_settings.json")
    assert not any(s.connected for s in by_key.values())


def test_the_cline_settings_path_can_be_overridden(tmp_path: Path) -> None:
    custom = tmp_path / "elsewhere" / "settings.json"
    env = make_env(tmp_path, CLINE_MCP_SETTINGS_PATH=str(custom))
    assert ac.cline_settings_path(env) == custom
    cline = next(s for s in ac.detect_agents(env) if s.key == "cline")
    assert cline.detected and cline.target == str(custom)


def test_qed_is_written_as_an_absolute_path_when_known(tmp_path: Path) -> None:
    assert ac.qed_command(make_env(tmp_path)) == QED
    assert ac.qed_command(make_env(tmp_path, have=())) == "qed"


def test_connected_is_detected_from_the_file(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    (env.home / ".cursor").mkdir()
    assert ac.connect("cursor", yes, env).changed
    assert next(s for s in ac.detect_agents(env) if s.key == "cursor").connected


# ------------------------------------------------------------------ the JSON files


def test_cursor_gets_a_new_file_with_a_preview_and_no_backup(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    (env.home / ".cursor").mkdir()
    result = ac.connect("cursor", yes, env)
    assert result.ok and result.changed and result.backup == ""
    assert json.loads(cursor_file(env).read_text()) == {
        "mcpServers": {"qedcode": {"command": QED, "args": ["mcp"]}}
    }
    (prompt,) = yes.prompts  # type: ignore[attr-defined]
    assert "Connect QEDCode to Cursor?" in prompt and "create" in prompt
    assert str(cursor_file(env)) in prompt and QED in prompt and "backup" not in prompt
    assert not list(cursor_file(env).parent.glob("*.tmp"))  # written in one step


def test_an_existing_file_is_merged_not_overwritten_and_backed_up(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    path = cursor_file(env)
    path.parent.mkdir()
    original = {"theme": "dark", "mcpServers": {"other": {"command": "x", "args": ["1"]}}}
    path.write_text(json.dumps(original))
    result = ac.connect("cursor", yes, env)
    assert result.ok and result.changed
    data = json.loads(path.read_text())
    assert (
        data["theme"] == "dark" and data["mcpServers"]["other"] == original["mcpServers"]["other"]
    )
    assert data["mcpServers"]["qedcode"] == {"command": QED, "args": ["mcp"]}
    assert (
        result.backup.endswith(".qedcode-backup-20231114-221320")
        or "qedcode-backup-" in result.backup
    )
    assert json.loads(Path(result.backup).read_text()) == original
    assert "change" in yes.prompts[0] and "backup copy" in yes.prompts[0]  # type: ignore[attr-defined]
    assert result.backup in result.message


def test_cline_entry_has_its_own_fields_and_uses_the_cli_settings_file(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    folder = env.home / ".cline" / "data" / "settings"
    folder.mkdir(parents=True)
    assert ac.connect("cline", yes, env).ok
    entry = json.loads((folder / "cline_mcp_settings.json").read_text())["mcpServers"]["qedcode"]
    assert entry == {"command": QED, "args": ["mcp"], "disabled": False, "autoApprove": []}


def test_already_connected_asks_nothing_and_changes_nothing(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    (env.home / ".cursor").mkdir()
    ac.connect("cursor", yes, env)
    before = cursor_file(env).read_text()
    result = ac.connect("cursor", never, env)
    assert result.ok and not result.changed and "already connected" in result.message
    assert cursor_file(env).read_text() == before
    assert not list(cursor_file(env).parent.glob("*backup*"))


def test_declining_changes_nothing(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    path = cursor_file(env)
    path.parent.mkdir()
    path.write_text('{"keep": 1}')
    result = ac.connect("cursor", no, env)
    assert not result.ok and "was not changed" in result.message
    assert path.read_text() == '{"keep": 1}' and not list(path.parent.glob("*backup*"))


def test_a_different_existing_entry_needs_a_second_yes(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    path = cursor_file(env)
    path.parent.mkdir()
    old = {"mcpServers": {"qedcode": {"command": "old-qed", "args": ["serve"]}}}
    path.write_text(json.dumps(old))
    answers = iter([True, False])
    declined = ac.connect("cursor", lambda prompt: next(answers), env)
    assert not declined.ok and "keeps its existing entry" in declined.message
    assert json.loads(path.read_text()) == old and not list(path.parent.glob("*backup*"))
    ac.connect("cursor", yes, env)
    assert len(yes.prompts) == 2 and "different 'qedcode' entry" in yes.prompts[1]  # type: ignore[attr-defined]
    assert json.loads(path.read_text())["mcpServers"]["qedcode"]["command"] == QED


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ('{\n  // my servers\n  "mcpServers": {}\n}', "not plain JSON"),
        ("[1, 2]", "not a JSON object"),
        ("{ broken", "not plain JSON"),
    ],
)
def test_a_file_that_is_not_plain_json_is_never_touched(
    tmp_path: Path, text: str, why: str
) -> None:
    env = make_env(tmp_path)
    path = cursor_file(env)
    path.parent.mkdir()
    path.write_text(text)
    result = ac.connect("cursor", never, env)
    assert not result.ok and not result.changed and why in result.message
    assert '"mcpServers"' in result.message and QED in result.message  # the snippet to paste
    assert path.read_text() == text and not list(path.parent.glob("*backup*"))


def test_an_empty_file_counts_as_an_empty_object(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    path = cursor_file(env)
    path.parent.mkdir()
    path.write_text("  \n")
    assert ac.connect("cursor", yes, env).changed
    assert "qedcode" in json.loads(path.read_text())["mcpServers"]


def test_mcpservers_of_the_wrong_type_is_replaced_by_an_object(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    path = cursor_file(env)
    path.parent.mkdir()
    path.write_text('{"mcpServers": []}')
    assert ac.connect("cursor", yes, env).changed
    assert list(json.loads(path.read_text())["mcpServers"]) == ["qedcode"]


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions and no root"
)
def test_a_folder_that_cannot_be_written_gives_a_snippet(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    folder = env.home / ".cursor"
    folder.mkdir()
    folder.chmod(0o500)
    try:
        result = ac.connect("cursor", yes, env)
    finally:
        folder.chmod(0o700)
    assert not result.ok and "Could not write" in result.message and "mcpServers" in result.message


def test_missing_settings_folders_say_what_to_do(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    cursor = ac.connect("cursor", never, env)
    assert (
        not cursor.ok
        and "settings were not found" in cursor.message
        and "mcpServers" in cursor.message
    )
    cline = ac.connect("cline", never, env)
    assert (
        not cline.ok and "editor extension" in cline.message and "docs.cline.bot" in cline.message
    )
    assert not cursor_file(env).exists()


# ------------------------------------------------------------------ Claude Code and OpenCode


def test_claude_code_runs_its_own_command_after_asking(tmp_path: Path) -> None:
    spy = Spy()
    env = make_env(tmp_path, have=("qed", "claude"), spy=spy)
    result = ac.connect("claude", yes, env)
    assert result.ok and result.changed and "ask it to use the qedcode tool" in result.message
    assert spy.commands == [["claude", "mcp", "add", "qedcode", "--", QED, "mcp"]]
    assert "(runs: claude mcp add qedcode -- " in yes.prompts[0]  # type: ignore[attr-defined]


def test_opencode_uses_the_documented_command_not_a_file_edit(tmp_path: Path) -> None:
    spy = Spy()
    env = make_env(tmp_path, have=("qed", "opencode"), spy=spy)
    assert ac.connect("opencode", yes, env).ok
    assert spy.commands == [["opencode", "mcp", "add", "qedcode", "--", QED, "mcp"]]
    assert not (env.home / ".config").exists()  # no config file was written by hand


def test_declined_commands_do_not_run(tmp_path: Path) -> None:
    spy = Spy()
    env = make_env(tmp_path, have=("qed", "claude"), spy=spy)
    assert not ac.connect("claude", no, env).ok and spy.commands == []


def test_a_failing_command_is_explained_and_an_existing_entry_is_fine(tmp_path: Path) -> None:
    env = make_env(tmp_path, have=("qed", "claude"), spy=Spy(ok=False, output="permission denied"))
    failed = ac.connect("claude", yes, env)
    assert not failed.ok and "did not work (permission denied)" in failed.message
    assert "claude mcp add qedcode" in failed.message
    exists = make_env(
        tmp_path,
        have=("qed", "claude"),
        spy=Spy(ok=False, output="MCP server qedcode already exists"),
    )
    assert ac.connect("claude", yes, exists).ok


def test_an_agent_that_is_not_installed_is_reported(tmp_path: Path) -> None:
    result = ac.connect("claude", never, make_env(tmp_path))
    assert not result.ok and "not found on your PATH" in result.message
    assert "docs.claude.com" in result.message


def test_an_unknown_agent_lists_the_choices(tmp_path: Path) -> None:
    result = ac.connect("vim", never, make_env(tmp_path))
    assert not result.ok and "claude, opencode, cline, cursor" in result.message


def test_snippets_are_valid_json_in_each_documented_shape() -> None:
    opencode = json.loads(ac.snippet("opencode", "qed"))
    assert opencode == {
        "mcp": {"servers": {"qedcode": {"type": "local", "command": ["qed", "mcp"]}}}
    }
    cursor = json.loads(ac.snippet("cursor", "qed"))
    assert cursor == {"mcpServers": {"qedcode": {"command": "qed", "args": ["mcp"]}}}
    assert json.loads(ac.snippet("cline", "qed"))["mcpServers"]["qedcode"]["disabled"] is False
    assert ac.snippet("claude", "qed") == "claude mcp add qedcode -- qed mcp"


# ------------------------------------------------------------------ the command


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch):
    spy = Spy()
    env = make_env(tmp_path, have=("qed", "claude"), spy=spy)
    (env.home / ".cursor").mkdir()
    monkeypatch.setattr("qedcode.agents_connect.Environment", lambda: env)
    return env, spy


def test_qed_connect_list(cli_env) -> None:
    result = CliRunner().invoke(app, ["connect", "--list"])
    assert result.exit_code == 0
    for name in ("Claude Code", "OpenCode", "Cline", "Cursor"):
        assert name in result.output
    assert "runs a command" in result.output


def test_qed_connect_everything_found_asks_each_time(cli_env) -> None:
    env, spy = cli_env
    result = CliRunner().invoke(app, ["connect"], input="y\ny\n")
    assert result.exit_code == 0, result.output
    assert (
        "Connected. In Claude Code" in result.output and "Cursor now lists QEDCode" in result.output
    )
    assert len(spy.commands) == 1 and cursor_file(env).exists()
    # Cursor is now recognised from its file; Claude Code can only be asked again (checking would
    # mean starting it), and its own command says "already exists" when it is connected
    again = CliRunner().invoke(app, ["connect"], input="n\n")
    assert "Connect QEDCode to Claude Code now?" in again.output
    assert "Connect QEDCode to Cursor" not in again.output
    assert next(s for s in ac.detect_agents(env) if s.key == "cursor").connected
    only_cursor = CliRunner().invoke(app, ["connect", "cursor"])
    assert "already connected" in only_cursor.output and only_cursor.exit_code == 0


def test_qed_connect_one_agent_with_yes_and_a_refusal_exit_code(cli_env) -> None:
    env, spy = cli_env
    done = CliRunner().invoke(app, ["connect", "cursor", "--yes"])
    assert done.exit_code == 0 and cursor_file(env).exists() and spy.commands == []
    declined = CliRunner().invoke(app, ["connect", "claude"], input="n\n")
    assert declined.exit_code == 1 and "was not changed" in declined.output and spy.commands == []
    missing = CliRunner().invoke(app, ["connect", "cline", "--yes"])
    assert missing.exit_code == 1 and "settings were not found" in missing.output


# ------------------------------------------------------------------ the setup wizard


def test_the_wizard_connects_every_agent_it_finds(tmp_path: Path) -> None:
    from qedcode import setup_wizard
    from tests.unit.test_setup_wizard import ScriptedPrompter

    env = make_env(tmp_path, have=("qed", "claude"))
    (env.home / ".cursor").mkdir()
    deps = setup_wizard.SetupDeps(
        which=env.which, home=lambda: env.home, run_command=env.run_command
    )
    io = ScriptedPrompter([True, False])  # Claude Code: yes, Cursor: no
    setup_wizard._step_agents(io, deps)
    assert env.run_command.commands == [["claude", "mcp", "add", "qedcode", "--", QED, "mcp"]]  # type: ignore[attr-defined]
    assert not cursor_file(env).exists()
    text = io.transcript
    assert "Connected. In Claude Code" in text and "Cursor was not changed" in text
    assert "Other agents:" in text and "OpenCode" in text and "qed connect" in text
    assert (
        "Claude Code: " not in text.split("Other agents:")[1]
    )  # already handled, not listed again
