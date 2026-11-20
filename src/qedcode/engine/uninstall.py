"""`qed uninstall`: remove QEDCode's files, after listing them and asking (AGENTS.md rule 7).

`qed uninstall` removes the project's `.qed/` folder, QEDCode's git refs and worktrees, and the
local `.git/info/exclude` line it added. With `--all` it also removes `~/.qedcode` (engines,
models, logs, config) and the API keys it saved in the system keychain. A QEDCode-owned model
server is stopped first. Your own files and your .gitignore are never touched.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from qedcode.config import qedcode_home
from qedcode.runtime.installer import format_size
from qedcode.vcs import worktree
from qedcode.vcs.git import git, git_dir, is_git_repo

QED_REF_PREFIX = "refs/qed/"


@dataclass
class UninstallItem:
    label: str
    bytes: int = 0
    detail: str = ""


@dataclass
class UninstallPlan:
    items: list[UninstallItem] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(i.bytes for i in self.items)

    @property
    def empty(self) -> bool:
        return not self.items

    def describe(self) -> str:
        lines = [
            f"  - {i.label}"
            + (f" ({format_size(i.bytes)})" if i.bytes else "")
            + (f": {i.detail}" if i.detail else "")
            for i in self.items
        ]
        return "\n".join(lines) + (
            f"\n  Total: {format_size(self.total_bytes)}" if self.total_bytes else ""
        )


def dir_size(path: Path) -> int:
    """Total size in bytes of every file under `path` (0 when it does not exist)."""
    if not path.exists():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())


def qed_refs(root: Path) -> list[str]:
    listed = git(root, "for-each-ref", "--format=%(refname)", QED_REF_PREFIX)
    return [r for r in listed.stdout.split("\n") if r.strip()]


def has_exclude_line(root: Path) -> bool:
    try:
        text = (git_dir(root) / "info" / "exclude").read_text(encoding="utf-8")
    except OSError:
        return False
    return any(line.strip() in (".qed", ".qed/") for line in text.splitlines())


def plan_project(root: Path | str) -> UninstallPlan:
    """What `qed uninstall` would remove in this project."""
    root = Path(root)
    plan = UninstallPlan()
    folder = root / ".qed"
    if folder.exists():
        plan.items.append(
            UninstallItem(".qed/ folder (settings, runs, receipts, worktrees)", dir_size(folder))
        )
    if is_git_repo(root):
        refs = qed_refs(root)
        if refs:
            plan.items.append(UninstallItem(f"{len(refs)} git ref(s) under refs/qed/ (snapshots)"))
        if has_exclude_line(root):
            plan.items.append(UninstallItem(".qed/ line in .git/info/exclude"))
    return plan


def plan_home(*, keys: list[str] | None = None) -> UninstallPlan:
    """What `qed uninstall --all` would remove from QEDCODE_HOME (and the keychain)."""
    home = qedcode_home()
    plan = UninstallPlan()
    names = {
        "bin": "downloaded llama.cpp engines",
        "models": "downloaded models",
        "logs": "log files",
        "run": "model server state",
    }
    for child in sorted(home.iterdir()) if home.exists() else []:
        label = names.get(child.name, child.name)
        size = dir_size(child) if child.is_dir() else child.stat().st_size
        plan.items.append(UninstallItem(f"{home / child.name} ({label})", size))
    if keys:
        plan.items.append(
            UninstallItem("saved API keys in the system keychain", 0, ", ".join(keys))
        )
    return plan


def saved_keys() -> list[str]:
    """Providers that have an API key saved by QEDCode in the keychain."""
    import keyring

    from qedcode.runtime.secrets import ENV_VARS, SERVICE

    found = []
    for provider in sorted(ENV_VARS):
        try:
            if keyring.get_password(SERVICE, provider):
                found.append(provider)
        except Exception:  # noqa: BLE001 - no keychain: nothing to list
            break
    return found


def stop_model_server() -> bool:
    """Stop the QEDCode-owned llama-server if one is running."""
    from qedcode.runtime.server_manager import ServerManager

    try:
        return ServerManager().stop()
    except Exception:  # noqa: BLE001 - never block an uninstall on the server
        return False


def uninstall_project(root: Path | str, confirm: Callable[[str], bool]) -> list[str]:
    """Remove the project's QEDCode files after asking. Returns what was done (empty = declined
    or nothing to remove)."""
    root = Path(root)
    plan = plan_project(root)
    if plan.empty:
        return []
    if not confirm(
        "This will remove from this project:\n"
        + plan.describe()
        + "\nYour own files stay untouched. Continue?"
    ):
        return []
    done: list[str] = []
    if stop_model_server():
        done.append("Stopped the model server.")
    if is_git_repo(root):
        worktree.remove_all(root)
        for ref in qed_refs(root):
            git(root, "update-ref", "-d", ref)
        done.append("Removed QEDCode's git refs and worktrees.")
        _drop_exclude_line(root)
    shutil.rmtree(root / ".qed", ignore_errors=True)
    if (root / ".qed").exists():
        done.append(
            "Could not remove everything in .qed/: a file there is still in use. Close other "
            "QEDCode windows in this project, then delete the .qed/ folder."
        )
    else:
        done.append("Removed the .qed/ folder.")
    return done


def _drop_exclude_line(root: Path) -> None:
    path = git_dir(root) / "info" / "exclude"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    kept = [ln for ln in lines if ln.strip() not in (".qed", ".qed/")]
    if kept != lines:
        path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")


def uninstall_all(
    root: Path | str | None,
    confirm: Callable[[str], bool],
    *,
    include_keys: bool = True,
) -> list[str]:
    """Remove the project's files and QEDCODE_HOME (and saved keys), asking once for all of it."""
    root_path = Path(root) if root is not None else None
    project = plan_project(root_path) if root_path is not None else UninstallPlan()
    keys = saved_keys() if include_keys else []
    home = plan_home(keys=keys)
    combined = UninstallPlan(items=[*project.items, *home.items])
    if combined.empty:
        return []
    if not confirm(
        "This will remove ALL of QEDCode's files:\n"
        + combined.describe()
        + "\nYour own project files stay untouched. Continue?"
    ):
        return []
    done: list[str] = []
    if stop_model_server():
        done.append("Stopped the model server.")
    if root_path is not None and not project.empty:
        done.extend(uninstall_project(root_path, lambda _prompt: True))
    for provider in keys:
        from qedcode.runtime.secrets import delete_api_key

        if delete_api_key(provider):
            done.append(f"Removed the saved API key for {provider}.")
    home_dir = qedcode_home()
    if home_dir.exists():
        from qedcode.logging_setup import close_logging

        close_logging()  # our own open log file would block the delete on Windows
        shutil.rmtree(home_dir, ignore_errors=True)
        if home_dir.exists():
            done.append(
                f"Could not remove everything in {home_dir}: a file there is still in use. "
                "Close other QEDCode windows (and the model server), then delete the folder."
            )
        else:
            done.append(f"Removed {home_dir}.")
    return done
