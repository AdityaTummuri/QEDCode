"""The repo map: every Python file with its top-level symbols, within a token budget.

It is the locator's only overview of the project, so it must stay small: first the symbol lists
shrink, then test files drop out, then files are cut from the end (with a note).
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

from qedcode.edits import js_symbols
from qedcode.project.paths import is_js_path, is_test_file
from qedcode.project.tokens import estimate_tokens
from qedcode.vcs.git import git

SKIP_DIRS = {
    ".git",
    ".qed",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".tox",
    "build",
    "dist",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "site-packages",
    ".eggs",
}
MAX_FILE_BYTES = 400_000
MAX_METHODS = 8


def list_source_files(root: Path | str, language: str = "python") -> list[str]:
    """Repo-relative paths of the project's source files (git's view when available), sorted.

    `language` is "python" (.py files) or "javascript" (JavaScript and TypeScript files)."""
    root = Path(root)

    def wanted(path: str) -> bool:
        if language == "javascript":
            return is_js_path(path) and not path.endswith((".d.ts", ".min.js"))
        return path.endswith(".py")

    listed = git(root, "ls-files", "-co", "--exclude-standard", "-z", exact=True)
    if listed.ok and listed.stdout:
        paths = [p for p in listed.stdout.split("\0") if wanted(p)]
        paths = [p for p in paths if not (set(p.split("/")[:-1]) & SKIP_DIRS)]
        return sorted(p for p in paths if (root / p).is_file())
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        found.extend(
            (Path(dirpath) / n).relative_to(root).as_posix() for n in filenames if wanted(n)
        )
    return sorted(found)


def list_python_files(root: Path | str) -> list[str]:
    """Repo-relative paths of the project's .py files (git's view when available), sorted."""
    return list_source_files(root, "python")


def top_level_symbols_js(source: str, lang: str, max_symbols: int = 12) -> list[str]:
    """Top-level names of a JS/TS file (a class lists a few of its methods)."""
    names = js_symbols.list_symbols(source, lang)
    out: list[str] = []
    for name in names:
        if "." not in name:
            members = [n.split(".", 1)[1] for n in names if n.startswith(name + ".")][:MAX_METHODS]
            out.append(f"{name}[{', '.join(members)}]" if members else name)
    return out[:max_symbols]


def symbols_for(rel: str, source: str, max_symbols: int = 12) -> list[str]:
    """The map entry of one file, by its language."""
    lang = js_symbols.language_for(rel)
    if lang:
        return top_level_symbols_js(source, lang, max_symbols)
    return top_level_symbols(source, max_symbols)


def top_level_symbols(source: str, max_symbols: int = 12) -> list[str]:
    """Names of the top-level functions and classes (a class lists a few of its methods)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            names.append(node.name)
        elif isinstance(node, ast.ClassDef):
            methods = [
                m.name
                for m in node.body
                if isinstance(m, ast.FunctionDef | ast.AsyncFunctionDef)
                and not (m.name.startswith("__") and m.name != "__init__")
            ][:MAX_METHODS]
            names.append(f"{node.name}[{', '.join(methods)}]" if methods else node.name)
    return names[:max_symbols]


def _read(root: Path, rel: str) -> str:
    try:
        path = root / rel
        if path.stat().st_size > MAX_FILE_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def render_map(entries: list[tuple[str, list[str]]], max_symbols: int, dropped: int = 0) -> str:
    lines = []
    for rel, symbols in entries:
        shown = symbols[:max_symbols]
        more = f", +{len(symbols) - len(shown)} more" if len(symbols) > len(shown) and shown else ""
        lines.append(f"{rel}: {', '.join(shown)}{more}" if shown else rel)
    if dropped:
        lines.append(f"... and {dropped} more files (not listed)")
    return "\n".join(lines)


def repo_map(
    root: Path | str,
    budget_tokens: int,
    files: list[str] | None = None,
    language: str = "python",
) -> str:
    """The map of the project, fitted to `budget_tokens` (estimated at 3.5 characters each)."""
    root = Path(root)
    paths = files if files is not None else list_source_files(root, language)
    entries = [(p, symbols_for(p, _read(root, p), 30)) for p in paths]
    for limit in (12, 6, 3, 0):
        text = render_map(entries, limit)
        if estimate_tokens(text) <= budget_tokens:
            return text
    ordered = sorted(entries, key=lambda e: is_test_file(e[0]))  # tests are dropped first
    kept = list(ordered)
    while (
        kept
        and estimate_tokens(render_map(sorted(kept), 0, len(entries) - len(kept))) > budget_tokens
    ):
        # drop from the end of the priority order: test files first, then later source files
        kept.pop()
    return render_map(sorted(kept), 0, len(entries) - len(kept))
