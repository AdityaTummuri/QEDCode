"""The public engine API: the only thing the TUI, the CLI and the MCP server call.

solve | check | verify | apply | undo | status. Progress goes out as Event objects through a
callback. This module only wires: the logic lives in the modules it calls.
"""

from __future__ import annotations

from pathlib import Path

from qedcode.vcs.git import is_git_repo, repo_root
from qedcode.verify.verify import VerifyResult, verify_receipt


def resolve_root(path: Path | str | None = None) -> Path:
    """The project root: the git top-level folder of `path` (default: the current folder)."""
    start = Path(path) if path is not None else Path.cwd()
    return repo_root(start) if is_git_repo(start) else start


def verify(receipt_path: Path | str, root: Path | str | None = None) -> VerifyResult:
    """Re-run a receipt's checks inside the repository and report VERIFIED or the mismatches."""
    base = Path(root) if root is not None else resolve_root()
    return verify_receipt(receipt_path, base)
