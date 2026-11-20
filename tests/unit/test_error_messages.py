"""AGENTS.md rule 12: every user-facing error says what happened, why, and what to do next.

A static check over the source: each message raised through one of the user-facing error classes
must contain a next step (an action word) or be built by a helper that adds one.
"""

from __future__ import annotations

import ast
from pathlib import Path

USER_FACING = {
    "ModelError",
    "ServerNotRunning",
    "ServerStartError",
    "InstallError",
    "InstallDeclined",
    "PatchRefused",
    "SecretsError",
    "CatalogError",
    "ProjectBusy",
    "Busy",
    "GitError",
}
ACTIONS = (
    "run ",
    "try ",
    "check ",
    "set ",
    "install",
    "use ",
    "make ",
    "wait",
    "see ",
    "start",
    "fix",
    "pick",
    "choose",
    "free",
    "add ",
    "send",
    "delete",
    "restart",
    "update",
    "open",
    "move",
    "remove",
    "download",
    "reinstall",
    "ask",
    "report",
    "paste",
    "pass ",
    "give ",
    "say ",
    "lower",
    "stop",
    "pull",
    "retry",
    "again",
    "apply ",
)
HELPERS = ("hint", "explain", "unreachable", "timed_out", "_explain_exit")
SRC = Path(__file__).resolve().parents[2] / "src" / "qedcode"


def raised_messages() -> list[tuple[str, int, str, str]]:
    found = []
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)):
                continue
            func = node.exc.func
            name = getattr(func, "id", getattr(func, "attr", ""))
            if name not in USER_FACING:
                continue
            text, delegated = [], False
            for sub in ast.walk(node.exc):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    text.append(sub.value)
                elif isinstance(sub, ast.Name) and (
                    sub.id == "exc" or any(h in sub.id.lower() for h in HELPERS)
                ):
                    delegated = True  # re-raises an earlier message, or adds a hint
                elif isinstance(sub, ast.Call):
                    callee = getattr(sub.func, "id", getattr(sub.func, "attr", ""))
                    if any(h in callee.lower() for h in HELPERS):
                        delegated = True
            message = " ".join(text)
            if delegated:
                message += " (builds its next step in a helper)"
            found.append((str(path.relative_to(SRC)), node.lineno, name, message))
    return found


def test_every_user_facing_error_names_a_next_step() -> None:
    rows = raised_messages()
    assert len(rows) > 40  # the scan really found the errors
    missing = [
        f"{file}:{line} {name}: {text[:110]}"
        for file, line, name, text in rows
        if "helper" not in text and not any(action in text.lower() for action in ACTIONS)
    ]
    assert missing == [], "These errors do not say what to do next:\n" + "\n".join(missing)
