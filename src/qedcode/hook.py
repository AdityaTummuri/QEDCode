"""`qed hook`: an agent's stop step runs `qed check` first (AGENTS.md 15.18).

A front door like `tour.py`: it only calls the engine API. Claude Code runs `qed hook run claude`
when the agent is about to stop; exit code 2 feeds the reason back and the agent keeps working.
A hook must never block an agent because of QEDCode itself, so anything unexpected lets it stop.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path

from qedcode.agents_connect import read_json, write_json
from qedcode.engine import api
from qedcode.engine.check import CheckOutcome
from qedcode.models.schemas import Speed

SETTINGS = Path(".claude") / "settings.json"
RUN_ARGS = ("hook", "run", "claude")
BLOCK = 2
TIMEOUT_S = 300


class HookError(RuntimeError):
    """The hook could not be installed or removed. The message says why and what to do."""


@dataclass
class HookChange:
    path: Path
    changed: bool
    message: str


# The settings file is usually committed, so the command names no path of this computer.
ENTRY = {
    "hooks": [{"type": "command", "command": f"qed {' '.join(RUN_ARGS)}", "timeout": TIMEOUT_S}]
}


def _is_ours(group: object) -> bool:
    if not isinstance(group, dict):
        return False
    return any(
        isinstance(h, dict) and str(h.get("command", "")).endswith(" ".join(RUN_ARGS))
        for h in group.get("hooks") or []
    )


def _settings(path: Path) -> dict:
    if not path.exists():
        return {}
    data, why = read_json(path)
    if data is None:
        raise HookError(
            f"{path} was left alone because {why}. Fix the file, or add the hook by hand: "
            "a Stop hook that runs `qed hook run claude`."
        )
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict) or not isinstance(hooks.get("Stop", []), list):
        raise HookError(
            f"{path} was left alone because its hooks are not in the shape Claude Code uses. "
            "Fix the file, then run the command again."
        )
    return data


def install(root: Path) -> HookChange:
    """Add the Stop hook to the project's Claude Code settings; everything else is kept."""
    path = root / SETTINGS
    data = _settings(path)
    entry = copy.deepcopy(ENTRY)
    stop = data.setdefault("hooks", {}).setdefault("Stop", [])
    if entry in stop:
        return HookChange(path, False, f"The hook is already in {path}.")
    stop[:] = [g for g in stop if not _is_ours(g)] + [entry]
    write_json(path, data)
    return HookChange(
        path,
        True,
        f"Added a Stop hook to {path}. From now on Claude Code runs `qed check` before it stops, "
        "and keeps working when the tests were weakened or a test broke.",
    )


def remove(root: Path) -> HookChange:
    """Take the Stop hook out again; other hooks and settings are kept."""
    path = root / SETTINGS
    data = _settings(path)
    hooks = data.get("hooks", {})
    stop = hooks.get("Stop", [])
    kept = [g for g in stop if not _is_ours(g)]
    if len(kept) == len(stop):
        return HookChange(path, False, f"There is no QEDCode hook in {path}.")
    if kept:
        hooks["Stop"] = kept
    else:
        hooks.pop("Stop")
    if not hooks:
        data.pop("hooks")
    write_json(path, data)
    return HookChange(path, True, f"Removed the QEDCode hook from {path}.")


def verdict(outcome: CheckOutcome) -> str:
    """Why the agent must keep working, in words it can act on ("" = it may stop)."""
    if outcome.tampered:
        lines = ["QEDCode: Caught. The tests were changed to look green:"]
        lines += [f"- {reason}" for reason in outcome.tampered[:5]]
        lines.append(
            "Put those tests back as they were, fix the code instead, and add a new test that "
            "fails before your fix and passes after it."
        )
        return "\n".join(lines)
    if outcome.status == "rejected" and outcome.failing_tests:
        lines = ["QEDCode: tests that passed before your change fail now:"]
        lines += [f"- {key}" for key in outcome.failing_tests[:5]]
        lines.append("Fix the code so they pass again. Do not edit or skip the tests.")
        return "\n".join(lines)
    return ""


def run_claude(stdin_text: str, cwd: Path) -> tuple[int, str]:
    """Handle one Claude Code Stop event: (exit code, text for stderr)."""
    try:
        event = json.loads(stdin_text) if stdin_text.strip() else {}
    except ValueError:
        event = {}
    if not isinstance(event, dict):
        event = {}
    if event.get("stop_hook_active"):
        return 0, ""  # it already kept working once because of a hook: never loop
    folder = Path(str(event.get("cwd") or cwd))
    try:
        outcome = api.check(folder if folder.is_dir() else cwd, speed=Speed.FAST)
    except Exception:  # noqa: BLE001 - a hook must never block an agent because of QEDCode
        return 0, ""
    reason = verdict(outcome)
    return (BLOCK, reason) if reason else (0, "")
