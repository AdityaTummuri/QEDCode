"""Context for prompts and checks (AGENTS.md sections 8 and 9).

This part: choosing which tests to run for a change. Selection rules, in order:
  1. the repro test file, if any;
  2. test files named after a changed module (test_<stem>.py or <stem>_test.py);
  3. test files that import a changed module;
  4. if nothing matched and the baseline full suite took 60 seconds or less, the full suite.
"""

from __future__ import annotations

import re
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
