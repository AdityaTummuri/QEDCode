"""Tests appended to an existing Python test file, recognised safely (used by `qed check`).

Writing a regression test next to the tests it belongs with is normal, so `qed check` should not
treat it as tampering. A modified test file counts as *only adding tests* when, comparing syntax
trees:

- every top-level statement of the old file is still there, unchanged and in the same order, and
- every new top-level statement is a `test*` function, a `Test*` class or an import, none of them
  re-binds a name the old file already defines (that could change how the old tests behave), and
  the only decorator allowed on them is `pytest.mark.parametrize`.

Anything else (an edited, removed or reordered statement, module-level code, an autouse fixture,
a skip) is not a pure addition and stays a protected change. When in doubt, the answer is no.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path

from qedcode.project.paths import is_test_file
from qedcode.vcs.git import GitError, git_out
from qedcode.verify.guard import FileDiff

ReadFn = Callable[[str], str]


def _bound_names(node: ast.stmt) -> set[str]:
    """Names a top-level statement binds in the module namespace."""
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return {node.name}
    if isinstance(node, ast.Import | ast.ImportFrom):
        return {(a.asname or a.name).split(".")[0] for a in node.names}
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
            names.add(child.id)
    return names


def _is_parametrize(decorator: ast.expr) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    return ast.unparse(target) in ("pytest.mark.parametrize", "mark.parametrize")


def _is_new_test(node: ast.stmt) -> bool:
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        return node.name.startswith("test") and all(_is_parametrize(d) for d in node.decorator_list)
    if isinstance(node, ast.ClassDef):
        return node.name.startswith("Test") and not node.decorator_list
    return False


def added_test_names(before: str, after: str) -> set[str] | None:
    """The names of the tests `after` adds to `before`, or None if it does more than add tests."""
    try:
        old = ast.parse(before).body
        new = ast.parse(after).body
    except (SyntaxError, ValueError):
        return None
    old_names: set[str] = set()
    for node in old:
        old_names |= _bound_names(node)
    added: set[str] = set()
    bound_now: set[str] = set(old_names)
    i = 0
    for node in new:
        if i < len(old) and ast.dump(node) == ast.dump(old[i]):
            i += 1
            continue
        names = _bound_names(node)
        if names & bound_now:
            return None  # re-binds an existing name (shadowing an old test or import)
        if isinstance(node, ast.Import | ast.ImportFrom):
            bound_now |= names
            continue
        if not _is_new_test(node):
            return None
        bound_now |= names
        added |= names
    if i != len(old):
        return None  # an old statement was changed, removed or moved
    return added or None


def appended_tests(
    files: list[FileDiff], read_before: ReadFn, read_after: ReadFn
) -> dict[str, set[str]]:
    """{path: new test names} for each modified Python test file that only gains tests."""
    found: dict[str, set[str]] = {}
    for f in files:
        if f.status != "modified" or f.binary or not f.path.endswith(".py"):
            continue
        if not is_test_file(f.path) or f.path.endswith("conftest.py"):
            continue
        try:
            names = added_test_names(read_before(f.path), read_after(f.path))
        except (GitError, OSError, UnicodeDecodeError):
            continue
        if names:
            found[f.path] = names
    return found


def appended_tests_in_worktree(
    root: Path, base_commit: str, wt: Path, files: list[FileDiff]
) -> dict[str, set[str]]:
    """`appended_tests` reading the old file from `base_commit` and the new one from `wt`."""
    return appended_tests(
        files,
        lambda path: git_out(root, "show", f"{base_commit}:{path}", strip=False),
        lambda path: (wt / path).read_text(encoding="utf-8"),
    )


def key_in_file(key: str, path: str) -> bool:
    """Is a JUnit key (`<module>[.<Class>]::<name>`) a test from the file at `path`?"""
    classname = key.rpartition("::")[0]
    dotted = path.removesuffix(".py").replace("/", ".")
    for module in (classname, classname.rpartition(".")[0]):
        if module and (dotted == module or dotted.endswith("." + module)):
            return True
    return False


def is_new_test_key(key: str, names: set[str]) -> bool:
    """Does a JUnit key (`<classname>::<name>`) belong to one of the added tests?"""
    classname, _, name = key.rpartition("::")
    if name.split("[", 1)[0] in names:
        return True
    owner = classname.rsplit(".", 1)[-1]  # a test class, or the module for plain functions
    return owner.startswith("Test") and owner in names
