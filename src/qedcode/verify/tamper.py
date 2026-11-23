"""Caught: did a change make the tests look green instead of fixing the code? (AGENTS.md 15.17)

Used by `qed check` only (role "user"): a model's own try that touches a protected test is rejected
at `scope` long before this. Two kinds of evidence, both deterministic:

- what the change did to existing tests and to test collection, read from the diff and from the
  syntax trees of the old and new test files;
- what the *original* versions of those test files say about the new code, by running them.

Precision matters more than reach: a deliberate change of behaviour edits tests too. So an edited
expectation counts only when the original test failed before the change and still fails after it
(the test was edited instead of the code fixed). Everything else is a note, never an accusation.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from qedcode.config import ProjectConfig
from qedcode.models.schemas import Baseline, CheckResult
from qedcode.project.context import imports_module, module_names
from qedcode.project.paths import is_js_path, is_test_file
from qedcode.vcs import worktree
from qedcode.vcs.git import git, git_out
from qedcode.verify.guard import (
    CAUGHT_MARKER,
    EXPECTATIONS_MARKER,
    WEAKENED_MARKER,
    FileDiff,
    is_test_path,
    weakened_from_checks,
    weakening_of,
)
from qedcode.verify.pytest_runner import failed_before
from qedcode.verify.runners import run_tests
from qedcode.verify.test_additions import key_in_file

MAX_REASONS = 8
DETAILS_LIMIT = 4000

_SKIP_WORDS = re.compile(r"\b(?:skip|skipif|xfail|skipIf|skipUnless|expectedFailure|skipTest)\b")
_COLLECTION_OFF = re.compile(
    r"\bcollect_ignore(?:_glob)?\b|\bdef\s+pytest_(?:ignore_collect|collection_modifyitems"
    r"|runtest_(?:makereport|call|protocol)|pyfunc_call|collect_file)\b"
)
_CONFIG_OFF = re.compile(
    r"--ignore(?:-glob)?\b|--deselect\b|--co(?:llect-only)?\b|(?<![\w-])-k\s|\bnorecursedirs\b"
    r"|\bcollect_ignore"
)
_PYTEST_CONFIGS = {"pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml", ".pytest.ini"}


@dataclass
class Tampering:
    """What was found. `caught` are reasons a person can check; `changed` are test keys whose
    original version passed before and fails now (a deliberate change of behaviour, or not)."""

    caught: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        out = [f"{CAUGHT_MARKER}{reason}" for reason in self.caught[:MAX_REASONS]]
        if self.changed:
            out.append(f"{EXPECTATIONS_MARKER}{', '.join(self.changed[:MAX_REASONS])}")
        return out

    def files(self) -> list[str]:
        """The files the reasons are about (each reason starts with its file)."""
        return sorted(
            {re.split(r":| was deleted", r, maxsplit=1)[0].split(" ")[0] for r in self.caught}
        )


def record(scope: CheckResult, found: Tampering) -> None:
    """Write what was found into the recorded scope check. A caught change also gets the
    weakened-tests marker, which is what `verify/grade.py` reads to cap the grade."""
    lines = found.lines()
    if found.caught and not weakened_from_checks([scope]):
        lines.append(f"{WEAKENED_MARKER} {', '.join(found.files())}")
    if lines:
        scope.details = "\n".join([*lines, scope.details]).strip()[:DETAILS_LIMIT]


# --------------------------------------------------------------------------- syntax trees


@dataclass
class _Test:
    name: str
    asserts: int
    always_true: int
    skips: int
    body: str


def _is_always_true(node: ast.Assert) -> bool:
    test = node.test
    if isinstance(test, ast.Tuple) and test.elts:
        return True
    if isinstance(test, ast.Constant):
        return bool(test.value)
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
        return any(isinstance(v, ast.Constant) and bool(v.value) for v in test.values)
    return False


def _is_assertion(node: ast.AST) -> bool:
    if isinstance(node, ast.Assert):
        return True
    if isinstance(node, ast.Call):
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        return name.startswith("assert") or name in ("raises", "warns")
    return False


def _skip_count(node: ast.AST) -> int:
    """Skip/xfail markers and calls anywhere in `node` (names only, never words in strings)."""
    count = 0
    for child in ast.walk(node):
        name = (
            child.attr
            if isinstance(child, ast.Attribute)
            else child.id
            if isinstance(child, ast.Name)
            else ""
        )
        if name and _SKIP_WORDS.fullmatch(name):
            count += 1
    return count


def _tests(tree: ast.Module) -> dict[str, _Test]:
    found: dict[str, _Test] = {}

    def add(node: ast.FunctionDef | ast.AsyncFunctionDef, prefix: str) -> None:
        name = f"{prefix}{node.name}"
        found[name] = _Test(
            name,
            sum(1 for n in ast.walk(node) if _is_assertion(n)),
            sum(1 for n in ast.walk(node) if isinstance(n, ast.Assert) and _is_always_true(n)),
            _skip_count(node),
            ast.dump(ast.Module(body=node.body, type_ignores=[])),
        )

    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if node.name.startswith("test"):
                add(node, "")
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef | ast.AsyncFunctionDef) and sub.name.startswith(
                    "test"
                ):
                    add(sub, f"{node.name}.")
    return found


def _outer_skips(tree: ast.Module) -> int:
    """Skips that switch off more than one test: module-level statements and class decorators."""
    count = 0
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            count += sum(_skip_count(d) for d in node.decorator_list)
            count += sum(_skip_count(s) for s in node.body if isinstance(s, ast.Assign | ast.Expr))
        elif isinstance(node, ast.Assign | ast.Expr | ast.If | ast.AnnAssign):
            count += _skip_count(node)
    return count


@dataclass
class _Edits:
    """What an edit did to one existing Python test file."""

    skipped: list[str] = field(default_factory=list)  # always Caught
    fewer_asserts: list[str] = field(default_factory=list)  # Caught unless the original passes
    removed: list[str] = field(default_factory=list)  # the same


def python_edits(before: str, after: str, elsewhere: Mapping[str, str]) -> _Edits | None:
    """Compare two versions of a Python test file; None when either cannot be parsed.

    `elsewhere` holds the bodies of tests in other changed test files (body -> name): a test that
    moved there, or that was only renamed, is not removed."""
    try:
        old_tree, new_tree = ast.parse(before), ast.parse(after)
    except (SyntaxError, ValueError):
        return None
    old, new = _tests(old_tree), _tests(new_tree)
    bodies = {t.body for t in new.values()} | set(elsewhere)
    edits = _Edits()
    for name, test in old.items():
        now = new.get(name)
        if now is None:
            if test.body not in bodies and name not in elsewhere.values():
                edits.removed.append(name)
            continue
        if now.skips > test.skips:
            edits.skipped.append(f"{name} is now skipped or expected to fail")
        if now.always_true > test.always_true:
            edits.skipped.append(f"{name} has an assert that can never fail")
        elif now.asserts < test.asserts:
            edits.fewer_asserts.append(name)
    if _outer_skips(new_tree) > _outer_skips(old_tree):
        edits.skipped.append("a whole group of tests is now skipped or expected to fail")
    return edits


# --------------------------------------------------------------------------- the analysis


ReadFn = Callable[[str], str]


def _try(read: ReadFn, path: str) -> str:
    try:
        return read(path)
    except Exception:  # noqa: BLE001 - binary, missing or unreadable: nothing to compare
        return ""


def _collection_switched_off(files: list[FileDiff]) -> list[str]:
    reasons = []
    for f in files:
        name = f.path.rsplit("/", 1)[-1]
        pattern = (
            _COLLECTION_OFF
            if name == "conftest.py"
            else _CONFIG_OFF
            if name in _PYTEST_CONFIGS
            else None
        )
        if pattern is None:
            continue
        before = " ".join(f.removed)
        for number, text in f.added:
            if text.lstrip().startswith(("#", ";")) or not pattern.search(text):
                continue
            match = pattern.search(text)
            assert match is not None
            if match.group(0) in before:  # an edited line that already had the option
                continue
            reasons.append(
                f"{f.path}:{number} changes which tests run or count ({text.strip()[:60]})"
            )
    return reasons


def _source_deleted(test_text: str, files: list[FileDiff], root: Path) -> bool:
    """The test imports a module the same change deletes: removing the test goes with it."""
    gone = [f.path for f in files if f.status == "deleted" and not is_test_path(f.path)]
    names = [n for p in gone if p.endswith(".py") for n in module_names(p, root)]
    return bool(names) and imports_module(test_text, names)


def _outcome_of(results: Mapping[str, str], path: str, name: str) -> str:
    """The outcome of test `name` ("Class.test" or "test") of `path`, or "" when unknown.
    A parametrized test failed when any of its cases did."""
    leaf = name.rsplit(".", 1)[-1]
    seen = [
        outcome
        for key, outcome in results.items()
        if key_in_file(key, path) and key.rsplit("::", 1)[-1].split("[", 1)[0] == leaf
    ]
    if not seen:
        return ""
    return next((o for o in seen if o in ("failed", "error")), seen[0])


def find_tampering(
    *,
    root: Path,
    base_commit: str,
    commit: str,
    files: list[FileDiff],
    appended: Iterable[str],
    baseline: Baseline,
    project: ProjectConfig,
    results: Mapping[str, str],
    run_dir: Path | None,
    name: str,
    cancelled: Callable[[], bool] | None = None,
) -> Tampering:
    """Everything that makes a user's change Caught. `results` are the change's own test outcomes
    (test key -> outcome) and `appended` the test files that only gained tests."""
    found = Tampering(caught=_collection_switched_off(files))
    touched = [
        f
        for f in files
        if is_test_file(f.path)
        and f.status in ("modified", "deleted")
        and not f.binary
        and f.path not in set(appended)
    ]
    if not touched:
        return found

    def old(path: str) -> str:
        return git_out(root, "show", f"{base_commit}:{path}", strip=False)

    def new(path: str) -> str:
        return git_out(root, "show", f"{commit}:{path}", strip=False)

    elsewhere: dict[str, dict[str, str]] = {}
    for f in files:
        if is_test_file(f.path) and f.path.endswith(".py") and f.status != "deleted":
            try:
                tests = _tests(ast.parse(_try(new, f.path)))
            except (SyntaxError, ValueError):
                continue
            elsewhere[f.path] = {t.body: n for n, t in tests.items()}

    originals = _run_originals(
        root, commit, base_commit, touched, project, run_dir, name, cancelled
    )
    base = dict(baseline.results)
    missing = [f.path for f in touched if not any(key_in_file(k, f.path) for k in base)]
    if missing and originals:
        base.update(
            _run_files(
                root, base_commit, None, missing, project, run_dir, f"{name}-base", cancelled
            )
        )

    for f in touched:
        before = _try(old, f.path)
        after = "" if f.status == "deleted" else _try(new, f.path)
        others = {
            body: test
            for path, tests in elsewhere.items()
            if path != f.path
            for body, test in tests.items()
        }
        edits = None if is_js_path(f.path) else python_edits(before, after, others)
        if f.status == "deleted":
            gone = list(_tests(ast.parse(before))) if edits is not None else []
            if edits is not None and gone and not edits.removed:
                continue  # every test moved to another file
            if _source_deleted(before, files, root):
                continue
            found.caught.append(f"{f.path} was deleted")
        elif edits is None:
            found.caught.extend(weakening_of(f))
        else:
            found.caught.extend(f"{f.path}: {reason}" for reason in edits.skipped)
            for test in edits.fewer_asserts:
                if _outcome_of(originals, f.path, test) != "passed":
                    found.caught.append(f"{f.path}: {test} checks less than it did")
            for test in edits.removed:
                if _outcome_of(originals, f.path, test) not in ("passed", "skipped"):
                    if not _source_deleted(before, files, root):
                        found.caught.append(
                            f"{f.path}: {test} was removed, and it does not pass with this change"
                        )
        if f.status == "deleted":
            continue
        file_base = {k: v for k, v in base.items() if key_in_file(k, f.path)}
        for key, outcome in originals.items():
            if not key_in_file(key, f.path) or outcome not in ("failed", "error"):
                continue
            if results.get(key) in ("failed", "error"):
                continue  # it fails as edited too: nothing is hidden
            if "::" not in key:
                continue  # the original file cannot be collected with the new code
            if failed_before(key, file_base):
                text = key.rsplit("::", 1)[-1]
                reason = f"{f.path}: {text} was edited to pass; as it was, it still fails"
                if not any(text.split("[", 1)[0] in c for c in found.caught):
                    found.caught.append(reason)
            else:
                found.changed.append(key)
    return found


def _run_files(
    root: Path,
    commit: str,
    restore_from: str | None,
    paths: list[str],
    project: ProjectConfig,
    run_dir: Path | None,
    name: str,
    cancelled: Callable[[], bool] | None,
) -> dict[str, str]:
    """Outcomes of `paths` at `commit`, after putting back their versions from `restore_from`."""
    checkout = worktree.create(root, commit, name)
    try:
        if restore_from is not None:
            put_back = [
                p for p in paths if git(checkout, "checkout", "-q", restore_from, "--", p).ok
            ]
        else:
            put_back = [p for p in paths if (checkout / p).is_file()]
        if not put_back:
            return {}
        outcome = run_tests(
            checkout,
            project,
            root,
            put_back,
            project.test_timeout_seconds,
            stage="targeted",
            run_dir=run_dir / name if run_dir else None,
            cancelled=cancelled,
        )
    finally:
        worktree.remove(root, checkout)
    if outcome.status in ("timeout", "cancelled", "error"):
        return {}
    return outcome.as_map()


def _run_originals(
    root: Path,
    commit: str,
    base_commit: str,
    touched: list[FileDiff],
    project: ProjectConfig,
    run_dir: Path | None,
    name: str,
    cancelled: Callable[[], bool] | None,
) -> dict[str, str]:
    return _run_files(
        root,
        commit,
        base_commit,
        [f.path for f in touched],
        project,
        run_dir,
        f"{name}-originals",
        cancelled,
    )
