"""Mutation-backed proofs (AGENTS.md section 15.9): how strongly do the new tests pin the fix?

Small mutants of only the changed lines are made with `ast`, and only the new tests run on each.
A mutant the tests catch (fail, error or time out) is "killed". The result is shown beside the
grade as `proof strength killed/total`; it never changes the grade.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from qedcode.config import ProjectConfig
from qedcode.models.schemas import MutationScore
from qedcode.project.paths import is_test_file
from qedcode.vcs import worktree
from qedcode.vcs.git import git
from qedcode.verify.guard import parse_diff
from qedcode.verify.runners import run_tests

MAX_MUTANTS = 12
MIN_TIMEOUT_S = 10.0

_SWAP_CMP: dict[type, type] = {
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
}
_SWAP_BIN: dict[type, type] = {
    ast.Add: ast.Sub,
    ast.Sub: ast.Add,
    ast.Mult: ast.Div,
    ast.Div: ast.Mult,
    ast.FloorDiv: ast.Mult,
}
_SYMBOL = {
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
    ast.Eq: "==",
    ast.NotEq: "!=",
    ast.In: "in",
    ast.NotIn: "not in",
    ast.Is: "is",
    ast.IsNot: "is not",
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.Div: "/",
    ast.FloorDiv: "//",
    ast.And: "and",
    ast.Or: "or",
}


@dataclass
class Mutant:
    path: str
    line: int
    description: str  # "line 7: `<` -> `<=`"
    source: str  # the whole mutated file


@dataclass
class _Site:
    """One place a mutation can be made: apply() changes the tree, undo() puts it back."""

    line: int
    kind: str
    description: str
    apply: Callable[[], None]
    undo: Callable[[], None]


def changed_source_lines(patch: str) -> dict[str, set[int]]:
    """Added or changed lines (new line numbers) per non-test Python file in a patch."""
    found: dict[str, set[int]] = {}
    for f in parse_diff(patch):
        if f.status == "deleted" or f.binary or not f.path.endswith(".py"):
            continue
        if is_test_file(f.path):
            continue
        lines = {number for number, _text in f.added}
        if lines:
            found[f.path] = lines
    return found


def _swap(holder: list, index: int, new: ast.AST) -> tuple[Callable[[], None], Callable[[], None]]:
    old = holder[index]

    def apply() -> None:
        holder[index] = new

    def undo() -> None:
        holder[index] = old

    return apply, undo


def _set(node: ast.AST, attr: str, new: object) -> tuple[Callable[[], None], Callable[[], None]]:
    old = getattr(node, attr)

    def apply() -> None:
        setattr(node, attr, new)

    def undo() -> None:
        setattr(node, attr, old)

    return apply, undo


def _sites(tree: ast.AST, lines: set[int]) -> Iterator[_Site]:
    """Every mutation site whose node starts on one of `lines`, in source order."""
    for node in ast.walk(tree):
        line = getattr(node, "lineno", None)
        if line not in lines:
            continue
        if isinstance(node, ast.Compare):
            for i, op in enumerate(node.ops):
                new = _SWAP_CMP.get(type(op))
                if new:
                    text = f"line {line}: `{_SYMBOL[type(op)]}` -> `{_SYMBOL[new]}`"
                    yield _Site(line, "compare", text, *_swap(node.ops, i, new()))
        elif isinstance(node, ast.BinOp) and type(node.op) in _SWAP_BIN:
            new = _SWAP_BIN[type(node.op)]
            text = f"line {line}: `{_SYMBOL[type(node.op)]}` -> `{_SYMBOL[new]}`"
            yield _Site(line, "arith", text, *_set(node, "op", new()))
        elif isinstance(node, ast.BoolOp):
            new = ast.Or if isinstance(node.op, ast.And) else ast.And
            text = f"line {line}: `{_SYMBOL[type(node.op)]}` -> `{_SYMBOL[new]}`"
            yield _Site(line, "bool", text, *_set(node, "op", new()))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            yield _Site(line, "not", f"line {line}: `not x` -> `x`", *_unwrap_not(tree, node))
        elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
            text = f"line {line}: `{node.value}` -> `{not node.value}`"
            yield _Site(line, "bool-const", text, *_set(node, "value", not node.value))
        elif (
            isinstance(node, ast.Constant) and type(node.value) is int and abs(node.value) < 10_000
        ):
            text = f"line {line}: `{node.value}` -> `{node.value + 1}`"
            yield _Site(line, "number", text, *_set(node, "value", node.value + 1))
        elif isinstance(node, ast.Return) and node.value is not None:
            is_none = isinstance(node.value, ast.Constant) and node.value.value is None
            if not is_none:
                text = f"line {line}: `return ...` -> `return None`"
                yield _Site(line, "return", text, *_set(node, "value", ast.Constant(None)))


def _unwrap_not(
    tree: ast.AST, target: ast.UnaryOp
) -> tuple[Callable[[], None], Callable[[], None]]:
    """Replace `not x` by `x` wherever the node is held (a field or a list element)."""
    for parent in ast.walk(tree):
        for field, value in ast.iter_fields(parent):
            if value is target:
                return _set(parent, field, target.operand)
            if isinstance(value, list):
                for i, item in enumerate(value):
                    if item is target:
                        return _swap(value, i, target.operand)
    return (lambda: None), (lambda: None)


def _spread(sites: list[_Site], limit: int) -> list[_Site]:
    """At most `limit` sites, taking one of each kind in turn so every operator is tried."""
    by_kind: dict[str, list[_Site]] = {}
    for site in sites:
        by_kind.setdefault(site.kind, []).append(site)
    chosen: list[_Site] = []
    while len(chosen) < limit and any(by_kind.values()):
        for kind in list(by_kind):
            if by_kind[kind] and len(chosen) < limit:
                chosen.append(by_kind[kind].pop(0))
    return sorted(chosen, key=lambda s: s.line)


def mutants_for(path: str, source: str, lines: set[int], limit: int = MAX_MUTANTS) -> list[Mutant]:
    """Mutants of `source` (the patched file) for nodes on `lines`; ones that do not compile
    are skipped."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[Mutant] = []
    for site in _spread(list(_sites(tree, lines)), limit):
        site.apply()
        try:
            text = ast.unparse(ast.fix_missing_locations(tree)) + "\n"
            compile(text, path, "exec")
        except (SyntaxError, ValueError, TypeError, RecursionError):
            text = ""
        finally:
            site.undo()
        if text:
            out.append(Mutant(path, site.line, site.description, text))
    return out


def mutation_score(
    root: Path | str,
    base_commit: str,
    patch: str,
    test_files: list[str],
    project: ProjectConfig,
    *,
    tests_seconds: float = 0.0,
    name: str = "mutate",
    cancelled: Callable[[], bool] | None = None,
    limit: int = MAX_MUTANTS,
) -> MutationScore | None:
    """Run the new tests against each mutant of the changed lines. None when nothing applies
    (no new tests, no changed Python source, JavaScript) or when cancelled."""
    if project.language != "python" or not test_files:
        return None
    changed = changed_source_lines(patch)
    if not changed:
        return None
    root = Path(root)
    wt = worktree.create(root, base_commit, f"{name}-mutants")
    try:
        applied = git(wt, "apply", "--binary", "--whitespace=nowarn", stdin_text=patch, exact=True)
        if not applied.ok:
            return None
        mutants: list[Mutant] = []
        for path, lines in sorted(changed.items()):
            file = wt / path
            if file.is_file():
                source = file.read_text(encoding="utf-8")
                mutants += mutants_for(path, source, lines, limit)
        mutants = mutants[:limit]
        if not mutants:
            return None
        timeout = min(
            float(project.test_timeout_seconds), max(MIN_TIMEOUT_S, 3 * tests_seconds or 0)
        )
        score = MutationScore(total=len(mutants))
        for mutant in mutants:
            if cancelled is not None and cancelled():
                return None
            target = wt / mutant.path
            original = target.read_text(encoding="utf-8")
            target.write_text(mutant.source, encoding="utf-8")
            try:
                out = run_tests(wt, project, root, test_files, timeout, cancelled=cancelled)
            finally:
                target.write_text(original, encoding="utf-8")
            if out.status == "cancelled":
                return None
            if out.status in ("failed", "collection_error", "timeout", "error") or out.failing:
                score.killed += 1
            else:
                score.survivors.append(f"{mutant.path} {mutant.description}")
        return score
    finally:
        worktree.remove(root, wt)


def strength_for(
    root: Path | str,
    receipt,
    project: ProjectConfig,
    *,
    cancelled: Callable[[], bool] | None = None,
) -> MutationScore | None:
    """Proof strength for a finished receipt: its new tests against mutants of its patch.

    Skipped (None) at fast speed, without fail-before-pass-after tests, or for JavaScript."""
    if receipt.task.speed.value == "fast" or not receipt.fbpa_tests or not receipt.tests_added:
        return None
    seconds = sum(c.duration_s for c in receipt.checks if c.stage == "fbpa")
    return mutation_score(
        root,
        receipt.base_commit,
        receipt.patch,
        list(receipt.tests_added),
        project,
        tests_seconds=seconds,
        name=receipt.task.id,
        cancelled=cancelled,
    )
