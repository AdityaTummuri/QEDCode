"""Snapshots: capture the project exactly as it is now without touching the user's files or index.

Recipe (AGENTS.md section 12): a temporary index file holds HEAD plus everything in the working
tree that git does not ignore; its tree becomes a commit kept under refs/qed/snapshots/<task id>.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from qedcode.vcs.git import git_dir, git_out, has_commits

SNAPSHOT_REF = "refs/qed/snapshots"
QED_DIR = ".qed"
# Never worth capturing even when a project forgot to ignore them (tracked files are unaffected).
HEAVY_DIRS = (
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
)


def ensure_excluded(root: Path) -> None:
    """Add `.qed/` to .git/info/exclude (local to this clone, never committed)."""
    exclude = git_dir(root) / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if any(line.strip() in (".qed", ".qed/", "/.qed", "/.qed/") for line in existing.splitlines()):
        return
    prefix = "" if existing.endswith("\n") or not existing else "\n"
    exclude.write_text(f"{existing}{prefix}.qed/\n", encoding="utf-8")


def snapshot_ref(task_id: str) -> str:
    return f"{SNAPSHOT_REF}/{task_id}"


def snapshot(root: Path | str, task_id: str) -> str:
    """Commit the project's current state (tracked + untracked, not ignored) and return its sha.

    The user's index, working tree and branches are never changed.
    """
    root = Path(root)
    ensure_excluded(root)
    tmp_dir = root / QED_DIR / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    index = tmp_dir / f"index-{task_id}"
    index.unlink(missing_ok=True)
    env = {"GIT_INDEX_FILE": str(index)}
    try:
        parent_args: list[str] = []
        if has_commits(root):
            git_out(root, "read-tree", "HEAD", env=env)
            parent_args = ["-p", "HEAD"]
        excludes = [f":(exclude,glob)**/{name}/**" for name in HEAVY_DIRS]
        git_out(root, "add", "-A", "--", ".", *excludes, env=env)
        tree = git_out(root, "write-tree", env=env)
        commit = git_out(
            root,
            "commit-tree",
            tree,
            *parent_args,
            "-m",
            f"qed snapshot {task_id}",
            identity=True,
        )
        git_out(root, "update-ref", snapshot_ref(task_id), commit)
        return commit
    finally:
        index.unlink(missing_ok=True)


def delete_snapshot(root: Path | str, task_id: str) -> None:
    """Remove a snapshot ref (its commit is garbage-collected by git later)."""
    from qedcode.vcs.git import git

    git(root, "update-ref", "-d", snapshot_ref(task_id))


def clear_tmp(root: Path | str) -> None:
    shutil.rmtree(Path(root) / QED_DIR / "tmp", ignore_errors=True)


CANDIDATE_REF = "refs/qed/candidates"


def candidate_ref(task_id: str, candidate_id: str) -> str:
    return f"{CANDIDATE_REF}/{task_id}/{candidate_id}"


def keep_candidate(root: Path | str, task_id: str, candidate_id: str, commit: str) -> None:
    """Keep a candidate's commit alive (so its worktree can be removed) under a private ref."""
    git_out(root, "update-ref", candidate_ref(task_id, candidate_id), commit)


def delete_candidate_refs(root: Path | str, task_id: str) -> int:
    """Delete every candidate ref of a task. Returns how many were removed."""
    from qedcode.vcs.git import git

    listed = git(root, "for-each-ref", "--format=%(refname)", f"{CANDIDATE_REF}/{task_id}/")
    refs = [r for r in listed.stdout.split("\n") if r.strip()]
    for ref in refs:
        git(root, "update-ref", "-d", ref)
    return len(refs)
