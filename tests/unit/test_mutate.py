"""Mutation-backed proofs (AGENTS.md 15.9): strong repro tests catch mutants, weak ones do not."""

from __future__ import annotations

import sys
from pathlib import Path

from qedcode import tour
from qedcode.config import ProjectConfig
from qedcode.models.schemas import MutationScore
from qedcode.vcs.git import git
from qedcode.verify.mutate import changed_source_lines, mutants_for, mutation_score
from qedcode.verify.receipt import describe, summary_line
from tests.unit.test_next_step import _receipt

SOURCE = """def f(a, b, items, flag):
    if a < b and a == 1:
        return a + b * 2
    if not flag or a in items:
        return True
    return a is None
"""


def test_each_operator_makes_one_mutant_on_the_changed_lines_only() -> None:
    every = {m.description for m in mutants_for("m.py", SOURCE, {2, 3, 4, 5, 6}, limit=50)}
    assert {
        "line 2: `<` -> `<=`",
        "line 2: `==` -> `!=`",
        "line 2: `and` -> `or`",
        "line 2: `1` -> `2`",
        "line 3: `+` -> `-`",
        "line 3: `*` -> `/`",
        "line 3: `return ...` -> `return None`",
        "line 4: `not x` -> `x`",
        "line 4: `or` -> `and`",
        "line 4: `in` -> `not in`",
        "line 5: `True` -> `False`",
        "line 6: `is` -> `is not`",
    } <= every
    only_three = mutants_for("m.py", SOURCE, {3})
    assert only_three and all(m.line == 3 for m in only_three)
    for mutant in only_three:
        compile(mutant.source, "m.py", "exec")
        assert mutant.source != SOURCE


def test_the_limit_spreads_over_operator_kinds() -> None:
    chosen = mutants_for("m.py", SOURCE, {2, 3, 4, 5, 6}, limit=4)
    kinds = {m.description.split(": ", 1)[1] for m in chosen}
    assert len(chosen) == 4 and len(kinds) == 4
    assert mutants_for("m.py", "def broken(:\n", {1}) == []


def test_changed_lines_skip_tests_and_deleted_files() -> None:
    patch = (
        "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -1,2 +1,2 @@\n"
        " x = 1\n-y = 2\n+y = 3\n"
        "diff --git a/tests/test_a.py b/tests/test_a.py\n--- a/tests/test_a.py\n"
        "+++ b/tests/test_a.py\n@@ -1 +1 @@\n-assert 1\n+assert 2\n"
    )
    assert changed_source_lines(patch) == {"src/a.py": {2}}


def _fixed_repo(tmp_path: Path, repro: str) -> tuple[Path, str, str]:
    repo = tour.prepare("007_sort_order", tmp_path)
    base = git(repo, "rev-parse", "HEAD").stdout.strip()
    scores = repo / "src" / "board" / "scores.py"
    # bytes, so the file keeps its line endings (on Windows text mode would rewrite every line)
    scores.write_bytes(
        scores.read_bytes().replace(b"sorted(scores)[:n]", b"sorted(scores, reverse=True)[:n]")
    )
    (repo / "tests" / "test_qed_repro.py").write_bytes(repro.encode("utf-8"))
    git(repo, "add", "-A")
    patch = git(repo, "diff", "--cached", "--binary", base, exact=True).stdout
    git(repo, "reset", "-q", "--hard", base)
    return repo, base, patch


def test_a_strong_repro_test_catches_the_mutants(tmp_path: Path) -> None:
    strong = (
        "from board.scores import top_scores\n\n\n"
        "def test_best_first():\n    assert top_scores([3, 9, 1, 7], 2) == [9, 7]\n"
    )
    repo, base, patch = _fixed_repo(tmp_path, strong)
    score = mutation_score(
        repo, base, patch, ["tests/test_qed_repro.py"], ProjectConfig(python=sys.executable)
    )
    assert score is not None and score.total >= 1 and score.killed == score.total
    assert not list((repo / ".qed" / "worktrees").glob("*"))  # cleaned up


def test_a_weak_repro_test_is_flagged_by_a_low_score(tmp_path: Path) -> None:
    weak = (
        "from board.scores import top_scores\n\n\n"
        "def test_returns_something():\n    assert top_scores([3, 9, 1]) is not None\n"
    )
    repo, base, patch = _fixed_repo(tmp_path, weak)
    score = mutation_score(
        repo, base, patch, ["tests/test_qed_repro.py"], ProjectConfig(python=sys.executable)
    )
    assert score is not None and score.killed < score.total
    assert score.survivors and "src/board/scores.py line" in score.survivors[0]


def test_nothing_to_measure_gives_none(tmp_path: Path) -> None:
    cfg = ProjectConfig(python=sys.executable)
    assert mutation_score(tmp_path, "HEAD", "", [], cfg) is None
    js = ProjectConfig(language="javascript", test_runner="node")
    assert mutation_score(tmp_path, "HEAD", "x", ["t.test.js"], js) is None


def test_the_receipt_shows_strength_beside_the_grade() -> None:
    receipt = _receipt("A", fbpa=["k"])
    receipt.mutation = MutationScore(killed=8, total=9, survivors=["src/a.py line 3: `+` -> `-`"])
    assert "proof strength 8/9" in summary_line(receipt)
    text = "\n".join(describe(receipt))
    assert "caught 8 of 9" in text and "survived: src/a.py line 3" in text
