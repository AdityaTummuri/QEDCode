"""Path helpers shared by the edit applier and the guards: validation and glob matching.

Paths inside QEDCode are repo-relative with forward slashes. Globs support `**` (any folders),
`*` (anything but a slash) and `?`.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

FORBIDDEN_TOP = (".git", ".qed")
# Characters git quotes in diff headers (so guards could misread the path) or that no tool expects.
_UNSAFE_CHARS = re.compile(r'[\x00-\x1f\x7f"]')
TEST_PATTERNS = ["tests/**", "test/**", "**/test_*.py", "**/*_test.py"]
JS_SUFFIXES = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts")
JS_TEST_PATTERNS = ["tests/**", "test/**", "**/__tests__/**", "**/*.test.*", "**/*.spec.*"]


@lru_cache(maxsize=256)
def _compile(pattern: str) -> re.Pattern[str]:
    out: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif ch == "*":
            out.append("[^/]*")
            i += 1
        elif ch == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(ch))
            i += 1
    return re.compile("".join(out) + r"\Z")


def glob_match(path: str, pattern: str) -> bool:
    """Does the repo-relative `path` match the glob `pattern`?"""
    return _compile(pattern.replace("\\", "/")).match(path.replace("\\", "/")) is not None


def matches_any(path: str, patterns: list[str]) -> bool:
    return any(glob_match(path, p) for p in patterns)


def is_protected(path: str, patterns: list[str]) -> bool:
    """True when `path` is one of the protected files (existing tests and test configuration)."""
    return matches_any(path, patterns)


def is_js_path(path: str) -> bool:
    """True for a JavaScript or TypeScript source file name."""
    return path.lower().endswith(JS_SUFFIXES)


def is_test_file(path: str) -> bool:
    """True for a Python, JavaScript or TypeScript test file by the usual names and folders."""
    if path.endswith(".py"):
        return matches_any(path, TEST_PATTERNS)
    return is_js_path(path) and matches_any(path, JS_TEST_PATTERNS)


def normalize_path(raw: str) -> str:
    """Tidy a model-written path: strip spaces and a leading ./ (nothing else)."""
    path = raw.strip()
    while path.startswith("./"):
        path = path[2:]
    return path


def validate_path(raw: str, root: Path | None = None) -> str | None:
    """Return a plain-English problem with `raw` as a repo-relative path, or None if it is fine."""
    path = raw.strip()
    if not path:
        return "the path is empty"
    if "\\" in path:
        return "use forward slashes in paths (for example app/auth.py)"
    if _UNSAFE_CHARS.search(path):
        return "the path contains quotes or control characters; use a plain file name"
    if path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        return "the path must be relative to the project, not absolute"
    parts = normalize_path(path).split("/")
    if ".." in parts:
        return "the path must stay inside the project (no ..)"
    if "" in parts[:-1] or parts == [""]:
        return "the path has an empty folder name"
    if any(part.startswith("-") for part in parts):
        return "file and folder names may not start with '-' (they would read as options)"
    if parts[0].lower() in FORBIDDEN_TOP:
        return f"files inside {parts[0]}/ cannot be edited"
    if root is not None:
        target = (root / normalize_path(path)).resolve()
        if not target.is_relative_to(root.resolve()):
            return "the path leads outside the project"
    return None
