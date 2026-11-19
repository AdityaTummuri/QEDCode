"""Context for prompts and checks (AGENTS.md sections 8 and 9).

This part: choosing which tests to run for a change. Selection rules, in order:
  1. the repro test file, if any;
  2. test files named after a changed module (test_<stem>.py or <stem>_test.py);
  3. test files that import a changed module;
  4. if nothing matched and the baseline full suite took 60 seconds or less, the full suite.
"""

from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass
from pathlib import Path

from qedcode.project.detect import find_test_files
from qedcode.project.paths import is_test_file

FULL_SUITE_FAST_SECONDS = 60.0
_SOURCE_ROOTS = ("src", "lib", "python")


def module_names(rel_path: str, repo: Path) -> list[str]:
    """Dotted module names a changed .py file may be imported as (several guesses, best first)."""
    path = Path(rel_path)
    if path.suffix != ".py":
        return []
    parts = list(path.with_suffix("").parts)
    is_init = parts[-1] == "__init__"
    if is_init:
        parts = parts[:-1]
    if not parts:
        return []
    # walk up while the folders are packages (have __init__.py): src/shop/pages.py -> shop.pages
    index = len(parts) - (0 if is_init else 1)
    while index > 0 and (repo / Path(*parts[:index]) / "__init__.py").exists():
        index -= 1
    names = [".".join(parts[index:]), ".".join(parts)]
    if parts[0] in _SOURCE_ROOTS and len(parts) > 1:
        names.append(".".join(parts[1:]))
    unique: list[str] = []
    for name in names:
        if name and name not in unique:
            unique.append(name)
    return unique


def imports_module(text: str, dotted_names: list[str]) -> bool:
    """Does Python source `text` import one of the dotted module names?

    Matches `import pkg.mod`, `from pkg.mod import x`, `from pkg import mod`, and a bare
    `import pkg` when the dotted name is then used (pkg.mod.attr).
    """
    for dotted in dotted_names:
        pattern = rf"(?m)^[ \t]*(?:from|import)[ \t]+{re.escape(dotted)}\b"
        if re.search(pattern, text):
            return True
        package, _, leaf = dotted.rpartition(".")
        if not package:
            continue
        from_import = (
            rf"(?m)^[ \t]*from[ \t]+{re.escape(package)}[ \t]+import[ \t]+"
            rf"[^\n#]*\b{re.escape(leaf)}\b"
        )
        if re.search(from_import, text):
            return True
        bare = rf"(?m)^[ \t]*import[ \t]+{re.escape(package)}[ \t]*(?:#.*)?$"
        if re.search(bare, text) and re.search(rf"\b{re.escape(dotted)}\b", text):
            return True
    return False


def related_tests(changed_paths: list[str], repo: Path | str) -> list[str]:
    """Test files (repo-relative, sorted) related to the changed files by name or by import."""
    repo = Path(repo)
    tests = find_test_files(repo)
    if not tests:
        return []
    chosen: set[str] = set()
    sources = [p for p in changed_paths if p.endswith(".py")]
    for changed in sources:
        if is_test_file(changed) and changed in tests:
            chosen.add(changed)
    stems = {Path(p).stem for p in sources if not is_test_file(p) and Path(p).stem != "__init__"}
    for test in tests:
        name = Path(test).stem
        if any(name == f"test_{stem}" or name == f"{stem}_test" for stem in stems):
            chosen.add(test)
    dotted = [n for p in sources if not is_test_file(p) for n in module_names(p, repo)]
    if dotted:
        for test in tests:
            if test in chosen:
                continue
            try:
                text = (repo / test).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if imports_module(text, dotted):
                chosen.add(test)
    return sorted(chosen)


def select_targeted(
    changed_paths: list[str],
    repo: Path | str,
    *,
    repro_files: list[str] | None = None,
    baseline_full_seconds: float | None = None,
) -> list[str] | None:
    """The tests to run for a candidate: repro first, then related files.

    Returns None to mean "run the full suite": nothing matched and the baseline full suite was
    quick (<= 60 s). Returns [] when nothing matched and the suite is slow or unknown.
    """
    repro = list(repro_files or [])
    related = [t for t in related_tests(changed_paths, repo) if t not in repro]
    selection = [*repro, *related]
    if not related and not repro:
        if baseline_full_seconds is not None and baseline_full_seconds <= FULL_SUITE_FAST_SECONDS:
            return None
        return []
    return selection


# --------------------------------------------------------------------------- prompt budget
# Prompt budget = model context - max output tokens - 512. Tokens are estimated as characters / 3.5.
# Over budget: drop whole files from the end of the locator's list first, then replace a large file
# with only its relevant symbols plus an outline of the rest.


CHARS_PER_TOKEN = 3.5
PROMPT_MARGIN_TOKENS = 512


def estimate_tokens(text: str) -> int:
    """Rough token count of `text` (characters divided by 3.5, rounded up)."""
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def prompt_budget(context_tokens: int, max_output_tokens: int) -> int:
    """Tokens available for the prompt: context - max output - 512 (never below 256)."""
    return max(256, context_tokens - max_output_tokens - PROMPT_MARGIN_TOKENS)


@dataclass
class ContextFile:
    """A file shown to the model; `condensed` means only relevant symbols plus an outline."""

    path: str
    content: str
    condensed: bool = False


def files_block(files: list[ContextFile]) -> str:
    """`=== path ===` followed by the content, for each file, without line numbers."""
    return "\n".join(f"=== {f.path} ===\n{f.content.rstrip(chr(10))}\n" for f in files).rstrip("\n")


def _header(lines: list[str], node: ast.AST) -> str:
    """The signature lines of a def/class (up to its first body statement)."""
    start = node.lineno - 1  # type: ignore[attr-defined]
    body = getattr(node, "body", [])
    end = (body[0].lineno - 1) if body else start + 1
    first = body[0] if body else None
    if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant):
        end = start + 1
        while end < len(lines) and not lines[end - 1].rstrip().endswith(":"):
            end += 1
    return "\n".join(lines[start:end]).rstrip()


def condense_file(content: str, keywords: set[str]) -> str:
    """Keep the module header and the relevant symbols in full; outline everything else."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return content
    lines = content.split("\n")
    wanted = {k.lower() for k in keywords if len(k) >= 3}

    def relevant(name: str) -> bool:
        low = name.lower()
        return any(k == low or (len(k) >= 4 and k in low) for k in wanted)

    out = ["# (outline view: bodies of unrelated functions were left out to fit the context)"]
    for node in tree.body:
        text = "\n".join(lines[node.lineno - 1 : node.end_lineno])
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            deco = [lines[d.lineno - 1] for d in node.decorator_list]
            if relevant(node.name):
                start = min([node.lineno, *[d.lineno for d in node.decorator_list]]) - 1
                out.append("\n".join(lines[start : node.end_lineno]))
            elif isinstance(node, ast.ClassDef):
                members = [
                    _header(lines, m) + "\n        ..."
                    for m in node.body
                    if isinstance(m, ast.FunctionDef | ast.AsyncFunctionDef)
                ]
                out.append(
                    "\n".join([*deco, _header(lines, node), *members]) or _header(lines, node)
                )
            else:
                out.append("\n".join([*deco, _header(lines, node), "    ..."]))
        else:
            out.append(text)
    return "\n".join(out) + "\n"


def fit_files(
    files: list[ContextFile], budget_tokens: int, keywords: set[str] | None = None
) -> list[ContextFile]:
    """Make the files fit `budget_tokens`: drop trailing files, then condense, then truncate."""
    keywords = keywords or set()
    chosen = list(files)

    def total(items: list[ContextFile]) -> int:
        return estimate_tokens(files_block(items))

    while len(chosen) > 1 and total(chosen) > budget_tokens:
        chosen.pop()
    if total(chosen) <= budget_tokens:
        return chosen
    chosen = [ContextFile(f.path, condense_file(f.content, keywords), True) for f in chosen]
    if total(chosen) <= budget_tokens:
        return chosen
    limit = max(int(budget_tokens * CHARS_PER_TOKEN) - 150 * len(chosen), 200) // max(
        len(chosen), 1
    )
    return [
        ContextFile(f.path, f.content[:limit].rsplit("\n", 1)[0] + "\n# (shortened to fit)\n", True)
        for f in chosen
    ]


def task_keywords(text: str) -> set[str]:
    """Identifier-like words from the task text (used to pick the relevant symbols)."""
    return {w for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text)}


def repro_test_path(request: str, existing_tests: list[str], repo: Path | str) -> str:
    """Where the repro test goes: tests/test_qed_<slug>.py, or next to the existing tests."""
    words = re.findall(r"[a-z0-9]+", request.lower())[:5]
    slug = "_".join(words) or "task"
    folder = "tests"
    if existing_tests:
        parents = [str(Path(t).parent.as_posix()) for t in existing_tests]
        folder = max(set(parents), key=parents.count)
        folder = "" if folder == "." else folder
    base = f"test_qed_{slug}"
    repo = Path(repo)
    for suffix in ("", "_2", "_3", "_4"):
        rel = f"{folder}/{base}{suffix}.py" if folder else f"{base}{suffix}.py"
        if not (repo / rel).exists():
            return rel
    return f"{folder}/{base}_x.py" if folder else f"{base}_x.py"
