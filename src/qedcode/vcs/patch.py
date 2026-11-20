"""Apply a finished result to the user's files, and take it out again (AGENTS.md section 12).

This module is the ONLY writer to the user's working tree. It refuses when a touched file changed
since the snapshot, never touches the git index, and records what it did so `undo_last` can reverse
it exactly.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from qedcode.vcs.git import git, git_out

LAST_APPLY = Path(".qed") / "last_apply.json"
NOTHING = "-"  # recorded hash for "this file did not exist"


class PatchRefused(RuntimeError):
    """Applying or undoing was refused to protect the user's files. The message says what to do."""

    def __init__(self, message: str, files: list[str] | None = None) -> None:
        super().__init__(message)
        self.files = files or []


@dataclass
class ApplyResult:
    task_id: str
    files: list[str]
    snapshot: str
    undo_file: Path
    notes: list[str] = field(default_factory=list)


def patch_files(root: Path | str, patch: str) -> list[str]:
    """The paths a patch touches, read by git itself (handles quoting and odd names)."""
    out = git_out(
        root, "apply", "--numstat", "-z", "--binary", stdin_text=patch, strip=False, exact=True
    )
    files: list[str] = []
    for record in out.split("\0"):
        if not record.strip("\n"):
            continue
        parts = record.lstrip("\n").split("\t", 2)
        if len(parts) == 3 and parts[2]:
            files.append(parts[2])
    return files


def _hash_working(root: Path, rel: str) -> str:
    """Hash a working-tree file the way git would (NOTHING when it is missing)."""
    path = root / rel
    if not path.is_file() and not path.is_symlink():
        return NOTHING
    return git_out(root, "hash-object", "--", rel)


def _hash_in_commit(root: Path, commit: str, rel: str) -> str:
    result = git(root, "rev-parse", "--verify", "--quiet", f"{commit}:{rel}")
    return result.stdout.strip() if result.ok else NOTHING


def _changed(root: Path, files: list[str], expected: dict[str, str]) -> list[str]:
    return [f for f in files if _hash_working(root, f) != expected[f]]


def apply_to_user(
    root: Path | str, snapshot: str, patch: str, *, task_id: str = "manual"
) -> ApplyResult:
    """Apply `patch` to the user's working tree if none of the files it touches changed.

    `snapshot` is the commit the patch was made against. Only the working tree is written, never
    the index. Raises PatchRefused (naming the files) instead of overwriting anything.
    """
    root = Path(root)
    if not patch.strip():
        raise PatchRefused("The change is empty, so there is nothing to apply. Run the task again.")
    files = patch_files(root, patch)
    before = {f: _hash_working(root, f) for f in files}
    expected = {f: _hash_in_commit(root, snapshot, f) for f in files}
    changed = [f for f in files if before[f] != expected[f]]
    if changed:
        raise PatchRefused(
            "Nothing was applied, because these files changed after QEDCode looked at your "
            f"project: {', '.join(changed)}. Your files were left alone. Run the task again, or "
            "apply the diff by hand.",
            changed,
        )
    check = git(root, "apply", "--check", "--binary", "--whitespace=nowarn", stdin_text=patch)
    if not check.ok:
        raise PatchRefused(
            "Nothing was applied, because the change no longer fits your files "
            f"({check.output.splitlines()[-1][:160] if check.output else 'git apply refused'}). "
            "Your files were left alone. Run the task again.",
            files,
        )
    done = git(root, "apply", "--binary", "--whitespace=nowarn", stdin_text=patch)
    if not done.ok:
        raise PatchRefused(
            f"Applying the change failed ({done.output[:160]}). Run `git status` to see your "
            "files, then run the task again.",
            files,
        )
    after = {f: _hash_working(root, f) for f in files}
    undo_file = root / LAST_APPLY
    undo_file.parent.mkdir(parents=True, exist_ok=True)
    undo_file.write_text(
        json.dumps(
            {
                "task_id": task_id,
                "snapshot": snapshot,
                "applied_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "files": files,
                "before": before,
                "after": after,
                "patch": patch,
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    return ApplyResult(task_id=task_id, files=files, snapshot=snapshot, undo_file=undo_file)


def last_apply(root: Path | str) -> dict | None:
    """The saved record of the last apply, or None when there is nothing to undo."""
    try:
        return json.loads((Path(root) / LAST_APPLY).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def undo_last(root: Path | str) -> list[str]:
    """Reverse the last apply if the files are still exactly as it left them. Returns the files."""
    root = Path(root)
    record = last_apply(root)
    if record is None:
        raise PatchRefused(
            "There is nothing to undo: QEDCode has not applied a change here. "
            "Apply a finished result first with `qed apply`."
        )
    files: list[str] = list(record["files"])
    changed = _changed(root, files, record["after"])
    if changed:
        raise PatchRefused(
            "Nothing was undone, because these files changed after the fix was applied: "
            f"{', '.join(changed)}. Your files were left alone. Undo those edits yourself first, "
            "or revert the files with git.",
            changed,
        )
    undone = git(root, "apply", "-R", "--binary", "--whitespace=nowarn", stdin_text=record["patch"])
    if not undone.ok:
        raise PatchRefused(
            f"Undoing the change failed ({undone.output[:160]}). Run `git status` to see your "
            "files; nothing else was changed.",
            files,
        )
    (root / LAST_APPLY).unlink(missing_ok=True)
    return files
