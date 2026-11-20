"""One-click agent connect (AGENTS.md section 15.4): register `qed mcp` with coding agents.

Claude Code and OpenCode are connected through their own documented commands. Cline's CLI settings
and Cursor's global MCP file are plain JSON files that are merged, never overwritten: a backup is
made first, a file that is not plain JSON is left alone, and nothing is changed without a yes.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from qedcode.verify.run import RunResult, run

SERVER_NAME = "qedcode"
AGENTS = ("claude", "opencode", "cline", "cursor")
NAMES = {"claude": "Claude Code", "opencode": "OpenCode", "cline": "Cline", "cursor": "Cursor"}
DOCS = {
    "claude": "https://docs.claude.com/en/docs/claude-code/mcp",
    "opencode": "https://opencode.ai/docs/mcp-servers",
    "cline": "https://docs.cline.bot/mcp/configuring-mcp-servers",
    "cursor": "https://cursor.com/docs (search for 'MCP')",
}
Confirm = Callable[[str], bool]


@dataclass
class AgentStatus:
    key: str
    name: str
    detected: bool
    connected: bool = False
    method: str = ""  # "command" or "file"
    target: str = ""  # the command that would run, or the file that would change
    note: str = ""


@dataclass
class ConnectResult:
    key: str
    ok: bool
    message: str
    changed: bool = False
    backup: str = ""


@dataclass
class Environment:
    """Everything connect reaches outside itself; replaced by fakes in tests."""

    home: Path = field(default_factory=Path.home)
    which: Callable[[str], str | None] = shutil.which
    run_command: Callable[[list[str]], RunResult] = lambda args: run(args, timeout=90)
    env: Mapping[str, str] = field(default_factory=lambda: os.environ)
    clock: Callable[[], float] = time.time


# --------------------------------------------------------------------------- where things are


def qed_command(env: Environment) -> str:
    """`qed` as an absolute path when it is on the PATH (editors do not see the shell's PATH)."""
    return env.which("qed") or "qed"


def cline_settings_path(env: Environment) -> Path:
    override = env.env.get("CLINE_MCP_SETTINGS_PATH")
    if override:
        return Path(override).expanduser()
    return env.home / ".cline" / "data" / "settings" / "cline_mcp_settings.json"


def cursor_settings_path(env: Environment) -> Path:
    return env.home / ".cursor" / "mcp.json"


def json_entry(key: str, qed: str) -> dict:
    """The server entry written into a JSON settings file."""
    entry: dict = {"command": qed, "args": ["mcp"]}
    if key == "cline":
        entry.update({"disabled": False, "autoApprove": []})
    return entry


def _file_for(key: str, env: Environment) -> Path | None:
    if key == "cline":
        return cline_settings_path(env)
    if key == "cursor":
        return cursor_settings_path(env)
    return None


def _command_for(key: str, qed: str) -> list[str]:
    if key == "claude":
        return ["claude", "mcp", "add", SERVER_NAME, "--", qed, "mcp"]
    return ["opencode", "mcp", "add", SERVER_NAME, "--", qed, "mcp"]


def snippet(key: str, qed: str = "qed") -> str:
    """Text a person can paste when automatic connecting is not possible."""
    if key == "opencode":
        entry = {"mcp": {"servers": {SERVER_NAME: {"type": "local", "command": [qed, "mcp"]}}}}
    elif key in ("cline", "cursor"):
        entry = {"mcpServers": {SERVER_NAME: json_entry(key, qed)}}
    else:
        return " ".join(_command_for("claude", qed))
    return json.dumps(entry)


# --------------------------------------------------------------------------- reading JSON files


def read_json(path: Path) -> tuple[dict | None, str]:
    """(object, '') for plain JSON, ({}, '') for a missing or empty file, else (None, why)."""
    if not path.exists():
        return {}, ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"it cannot be read ({exc})"
    if not text.strip():
        return {}, ""
    try:
        data = json.loads(text)
    except ValueError:
        return None, "it is not plain JSON (it may contain comments)"
    if not isinstance(data, dict):
        return None, "its content is not a JSON object"
    return data, ""


def _current_entry(data: dict) -> dict | None:
    servers = data.get("mcpServers")
    if isinstance(servers, dict) and isinstance(servers.get(SERVER_NAME), dict):
        return servers[SERVER_NAME]
    return None


def _same_entry(found: dict, wanted: dict) -> bool:
    return found.get("command") == wanted.get("command") and found.get("args") == wanted.get("args")


# --------------------------------------------------------------------------- detection


def detect_agents(env: Environment | None = None) -> list[AgentStatus]:
    """Which agents are on this computer, and is QEDCode already registered with them?"""
    env = env or Environment()
    qed = qed_command(env)
    found: list[AgentStatus] = []
    for key in AGENTS:
        name = NAMES[key]
        if key in ("claude", "opencode"):
            binary = env.which(key)
            command = " ".join(_command_for(key, qed))
            found.append(AgentStatus(key, name, bool(binary), method="command", target=command))
            continue
        path = _file_for(key, env)
        assert path is not None
        present = path.parent.is_dir() or (key == "cline" and "CLINE_MCP_SETTINGS_PATH" in env.env)
        status = AgentStatus(key, name, present, method="file", target=str(path))
        data, _why = read_json(path)
        if data is not None:
            entry = _current_entry(data)
            status.connected = entry is not None and _same_entry(entry, json_entry(key, qed))
        found.append(status)
    return found


# --------------------------------------------------------------------------- connecting


def _backup(path: Path, env: Environment) -> str:
    if not path.exists():
        return ""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(env.clock()))
    target = path.with_name(f"{path.name}.qedcode-backup-{stamp}")
    shutil.copy2(path, target)
    return str(target)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.qedcode-{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def connect_file(key: str, confirm: Confirm, env: Environment) -> ConnectResult:
    path = _file_for(key, env)
    assert path is not None
    name, qed = NAMES[key], qed_command(env)
    wanted = json_entry(key, qed)
    data, why = read_json(path)
    if data is None:
        return ConnectResult(
            key,
            False,
            f"{path} was left alone because {why}. Add this to it yourself:\n  {snippet(key, qed)}",
        )
    existing = _current_entry(data)
    if existing is not None and _same_entry(existing, wanted):
        return ConnectResult(key, True, f"{name} is already connected to QEDCode.")
    preview = json.dumps({SERVER_NAME: wanted})
    if not confirm(
        f"Connect QEDCode to {name}? This will {'create' if not path.exists() else 'change'} "
        f'{path} and add: "mcpServers" -> {preview}'
        + (" (a backup copy of the file is made first)" if path.exists() else "")
    ):
        return ConnectResult(key, False, f"Cancelled. {name} was not changed.")
    if existing is not None and not confirm(
        f"{path} already has a different '{SERVER_NAME}' entry ({json.dumps(existing)}). "
        "Replace it?"
    ):
        return ConnectResult(key, False, f"Cancelled. {name} keeps its existing entry.")
    backup = _backup(path, env)
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    servers[SERVER_NAME] = wanted
    data["mcpServers"] = servers
    try:
        _write_json(path, data)
    except OSError as exc:
        return ConnectResult(
            key,
            False,
            f"Could not write {path} ({exc}). Check the folder's permissions, or add "
            f"this yourself:\n  {snippet(key, qed)}",
        )
    where = f" (the old file is saved as {backup})" if backup else ""
    return ConnectResult(
        key, True, f"Connected. {name} now lists QEDCode in {path}{where}.", True, backup
    )


def connect_command(key: str, confirm: Confirm, env: Environment) -> ConnectResult:
    name, qed = NAMES[key], qed_command(env)
    command = _command_for(key, qed)
    if not confirm(f"Connect QEDCode to {name} now? (runs: {' '.join(command)})"):
        return ConnectResult(key, False, f"Cancelled. {name} was not changed.")
    done = env.run_command(command)
    if done.ok:
        return ConnectResult(
            key, True, f"Connected. In {name}, ask it to use the qedcode tool.", True
        )
    reason = (done.error or done.output)[:160]
    if "already exists" in reason.lower():
        return ConnectResult(key, True, f"{name} is already connected to QEDCode.")
    return ConnectResult(
        key,
        False,
        f"That did not work ({reason}). You can run the command yourself later: "
        f"{' '.join(command)}",
    )


def connect(key: str, confirm: Confirm, env: Environment | None = None) -> ConnectResult:
    """Connect QEDCode to one agent after showing exactly what will change and asking."""
    env = env or Environment()
    if key not in AGENTS:
        return ConnectResult(
            key,
            False,
            f"'{key}' is not an agent QEDCode knows. Choose one of: {', '.join(AGENTS)}.",
        )
    if key in ("claude", "opencode"):
        if not env.which(key):
            return ConnectResult(
                key,
                False,
                f"{NAMES[key]} was not found on your PATH. Install it first, or add "
                f"QEDCode by hand ({DOCS[key]}).",
            )
        return connect_command(key, confirm, env)
    path = _file_for(key, env)
    assert path is not None
    if not (path.parent.is_dir() or (key == "cline" and "CLINE_MCP_SETTINGS_PATH" in env.env)):
        return ConnectResult(
            key,
            False,
            f"{NAMES[key]} settings were not found ({path.parent} does not exist). If you use "
            f"its editor extension, add QEDCode there ({DOCS[key]}):\n"
            f"  {snippet(key, qed_command(env))}",
        )
    return connect_file(key, confirm, env)
