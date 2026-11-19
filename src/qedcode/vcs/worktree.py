"""Worktrees: one throwaway checkout per candidate under .qed/worktrees/ (AGENTS.md section 12)."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from qedcode.vcs.git import git, git_out

WORKTREES_DIR = Path(".qed") / "worktrees"
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")


def worktrees_root(root: Path | str) -> Path:
    return Path(root) / WORKTREES_DIR


def safe_name(name: str) -> str:
    """A folder name that cannot escape .qed/worktrees/."""
    cleaned = _SAFE_NAME.sub("-", name).strip(".-") or "wt"
    return cleaned[:80]


def prune(root: Path | str) -> None:
    """Forget worktrees whose folders are gone (run at the start of every task)."""
    git(root, "worktree", "prune")


def create(root: Path | str, commit: str, name: str) -> Path:
    """Check out `commit` (detached) into .qed/worktrees/<name> and return the folder."""
    root = Path(root)
    path = worktrees_root(root) / safe_name(name)
    if path.exists():
        remove(root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    git_out(root, "worktree", "add", "--detach", "--force", str(path), commit)
    return path


def commit_all(path: Path | str, message: str) -> str:
    """Commit everything in a worktree (even nothing) and return the new commit sha."""
    git_out(path, "add", "-A")
    git_out(
        path,
        "commit",
        "--allow-empty",
        "--no-verify",
        "--no-gpg-sign",
        "-q",
        "-m",
        message,
        identity=True,
    )
    return git_out(path, "rev-parse", "HEAD")


def diff(root: Path | str, base: str, commit: str) -> str:
    """The patch from `base` to `commit`, in a form `git apply` accepts (binary-safe)."""
    return git_out(
        root,
        "diff",
        "--binary",
        "--no-color",
        "--no-ext-diff",
        "--no-textconv",
        "--no-renames",
        "--src-prefix=a/",
        "--dst-prefix=b/",
        base,
        commit,
        strip=False,
        exact=True,
    )


def remove(root: Path | str, path: Path | str) -> None:
    """Delete a worktree: first through git, then by force if the folder is still there."""
    path = Path(path)
    git(root, "worktree", "remove", "--force", str(path))
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    prune(root)


def remove_all(root: Path | str) -> int:
    """Remove every worktree under .qed/worktrees/. Returns how many folders were removed."""
    folder = worktrees_root(root)
    count = 0
    if folder.exists():
        for child in sorted(folder.iterdir()):
            remove(root, child)
            count += 1
        shutil.rmtree(folder, ignore_errors=True)
    prune(root)
    return count


def list_worktrees(root: Path | str) -> list[Path]:
    """The folders currently under .qed/worktrees/ (for tests and `qed doctor`)."""
    folder = worktrees_root(root)
    return sorted(p for p in folder.iterdir()) if folder.exists() else []
