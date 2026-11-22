"""Smart Questions (AGENTS.md section 15.14): when two passing fixes differ, ask "A or B?".

Both candidates run on generated inputs through a QEDCode-written pytest file (the same allowed
command as every test run). The first input where they disagree becomes one concrete question,
and the answer becomes a test.
"""

from __future__ import annotations

import ast
import itertools
import json
from dataclasses import dataclass
from pathlib import Path

from qedcode.config import ProjectConfig
from qedcode.project.context import module_names
from qedcode.project.paths import is_test_file
from qedcode.vcs import worktree
from qedcode.verify.guard import parse_diff
from qedcode.verify.runners import run_tests

POOL: list[object] = [[], [1, 2, 3], 0, 1, -1, 2, 10, "", "abc", None]
MAX_CALLS = 300
PROBE_FILE = "tests/test_qed_probe.py"
PROBE_OUT = ".qed_probe.json"


@dataclass
class Disagreement:
    path: str
    module: str
    func: str
    args: tuple
    first: tuple[str, str]  # ("ok", repr) or ("raises", ExceptionName)
    second: tuple[str, str]

    def call(self) -> str:
        return f"{self.func}({', '.join(repr(a) for a in self.args)})"

    @staticmethod
    def describe(outcome: tuple[str, str]) -> str:
        kind, value = outcome
        return f"raise {value}" if kind == "raises" else f"return {value}"

    def question(self) -> str:
        return (
            f"Two fixes pass every test but disagree. For `{self.call()}`, should it "
            f"{self.describe(self.first)} (fix 1) or {self.describe(self.second)} (fix 2)? "
            "Answer 1 or 2."
        )


def changed_functions(diff: str, source: str) -> list[str]:
    """Top-level functions in `source` whose lines the diff touches."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    lines: set[int] = set()
    for f in parse_diff(diff):
        lines |= {n for n, _ in f.added}
    out = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            end = node.end_lineno or node.lineno
            if any(node.lineno <= n <= end for n in lines):
                out.append(node.name)
    return out


def required_params(source: str, func: str) -> int:
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name == func:
            args = node.args
            positional = args.posonlyargs + args.args
            return len(positional) - len(args.defaults)
    return 0


def probe_inputs(count: int) -> list[tuple]:
    """Fixed inputs for `count` required parameters, at most MAX_CALLS of them."""
    if count <= 0:
        return []
    return list(itertools.islice(itertools.product(POOL, repeat=count), MAX_CALLS))


def probe_test(module: str, func: str, inputs: list[tuple], out: str) -> str:
    """A pytest file that calls `func` on every input and writes the outcomes as JSON."""
    return f"""import copy
import json

from {module} import {func} as _target

_INPUTS = {inputs!r}


def test_qed_probe():
    outcomes = []
    for args in _INPUTS:
        try:
            outcomes.append(["ok", repr(_target(*copy.deepcopy(args)))[:300]])
        except Exception as exc:  # noqa: BLE001 - every outcome is recorded
            outcomes.append(["raises", type(exc).__name__])
    with open({out!r}, "w", encoding="utf-8") as handle:
        json.dump(outcomes, handle)
"""


def run_probe(
    root: Path, commit: str, project: ProjectConfig, module: str, func: str, inputs: list[tuple]
) -> list[tuple[str, str]] | None:
    """Outcomes of `func` on `inputs` in a fresh worktree of `commit`; None if it did not run."""
    wt = worktree.create(root, commit, f"probe-{commit[:10]}-{func}")
    try:
        out_file = wt / PROBE_OUT
        (wt / PROBE_FILE).parent.mkdir(parents=True, exist_ok=True)
        (wt / PROBE_FILE).write_text(
            probe_test(module, func, inputs, str(out_file)), encoding="utf-8"
        )
        timeout = float(min(project.test_timeout_seconds, 60))
        run_tests(wt, project, root, [PROBE_FILE], timeout)
        try:
            data = json.loads(out_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return [(str(k), str(v)) for k, v in data] if len(data) == len(inputs) else None
    finally:
        worktree.remove(root, wt)


def _testable(outcome: tuple[str, str]) -> bool:
    kind, value = outcome
    if kind == "raises":  # a TypeError only says the generated input had the wrong type
        return value.isidentifier() and value != "TypeError"
    try:
        ast.literal_eval(value)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return False
    return True


def first_disagreement(
    inputs: list[tuple], a: list[tuple[str, str]], b: list[tuple[str, str]]
) -> int | None:
    """Index of the first input where the outcomes differ and both can be written as a test."""
    for index, (x, y) in enumerate(zip(a, b, strict=False)):
        if x != y and _testable(x) and _testable(y):
            return index
    return None


def find_disagreement(
    root: Path, project: ProjectConfig, first: tuple[str, str], second: tuple[str, str]
) -> Disagreement | None:
    """`first`/`second` are (commit, diff) of two passing candidates."""
    (commit_a, diff_a), (commit_b, diff_b) = first, second
    paths = {
        f.path
        for f in parse_diff(diff_a)
        if f.path.endswith(".py") and not is_test_file(f.path) and f.status != "deleted"
    } & {f.path for f in parse_diff(diff_b)}
    from qedcode.vcs.git import git

    for path in sorted(paths):
        shown = git(root, "show", f"{commit_a}:{path}", exact=True)
        if not shown.ok:
            continue
        funcs = set(changed_functions(diff_a, shown.stdout))
        names = module_names(path, root)
        if not names:
            continue
        for func in sorted(funcs):
            inputs = probe_inputs(required_params(shown.stdout, func))
            if not inputs:
                continue
            got_a = run_probe(root, commit_a, project, names[0], func, inputs)
            got_b = run_probe(root, commit_b, project, names[0], func, inputs) if got_a else None
            if not got_a or not got_b:
                continue
            index = first_disagreement(inputs, got_a, got_b)
            if index is not None:
                return Disagreement(path, names[0], func, inputs[index], got_a[index], got_b[index])
    return None


def answer_test(d: Disagreement, chosen: tuple[str, str]) -> str:
    """Test code (to append to the repro test) that pins the chosen behaviour."""
    call = d.call()
    name = f"test_qed_answer_{d.func}"
    doc = f'    """The answer to: what should {call} do? ({Disagreement.describe(chosen)})"""\n'
    head = f"\n\ndef {name}():\n{doc}    from {d.module} import {d.func}\n\n"
    kind, value = chosen
    if kind == "raises":
        return (
            head
            + "    import pytest\n\n"
            + f"    with pytest.raises(Exception) as info:\n        {call}\n"
            + f"    assert type(info.value).__name__ == {value!r}\n"
        )
    return head + f"    assert {call} == {value}\n"


def parse_answer(text: str | None) -> int | None:
    """1 or 2 from a reply like '1', 'fix 2', 'B'; None when it is no clear choice."""
    if not text:
        return None
    lowered = text.strip().lower()
    for choice, words in (
        (1, ("1", "a", "fix 1", "one", "first")),
        (2, ("2", "b", "fix 2", "two", "second")),
    ):
        if lowered in words or lowered.startswith(words[0] + " ") or lowered.startswith(words[2]):
            return choice
    return None
