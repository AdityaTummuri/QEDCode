"""Smart Questions (AGENTS.md 15.14): two passing fixes that disagree lead to one question."""

from __future__ import annotations

import ast
from pathlib import Path

import yaml

from qedcode import tour
from qedcode.engine import api
from qedcode.engine.callbacks import NonInteractiveCallbacks
from qedcode.engine.questions import (
    Disagreement,
    answer_test,
    changed_functions,
    first_disagreement,
    parse_answer,
    probe_inputs,
    probe_test,
    required_params,
)
from qedcode.models.schemas import Grade, Speed
from qedcode.runtime.fake import FakeModel

ALT_FIX = """PLAN:
- slice exactly `size` items, and refuse pages below 1

EDIT search_replace path=src/shop/pages.py
<<<<<<< SEARCH
    start = (page - 1) * size
    end = start + size - 1
=======
    if page < 1:
        raise ValueError("pages start at 1")
    start = (page - 1) * size
    end = start + size
>>>>>>> REPLACE
"""


class Answering(NonInteractiveCallbacks):
    def __init__(self, reply: str | None) -> None:
        super().__init__()
        self.reply = reply
        self.asked: list[str] = []

    def ask(self, question: str) -> str | None:
        self.asked.append(question)
        return self.reply


def _model() -> FakeModel:
    script = yaml.safe_load((tour.TOUR_DIR / "001_paginate_off_by_one" / "script.yaml").read_text())
    good = script["coder"][1]
    script["coder"] = [good, ALT_FIX]
    return FakeModel(script)


def test_inputs_params_and_changed_functions() -> None:
    assert probe_inputs(0) == [] and len(probe_inputs(1)) == 10
    assert len(probe_inputs(3)) == 300 and probe_inputs(2)[0] == ([], [])
    source = "def f(a, b=1):\n    return a\n\n\ndef g(x):\n    return x\n"
    assert required_params(source, "f") == 1 and required_params(source, "g") == 1
    diff = "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n@@ -5,2 +5,2 @@\n def g(x):\n-    return 0\n+    return x\n"
    assert changed_functions(diff, source) == ["g"]
    compile(probe_test("m", "g", [(1,), ([],)], "/tmp/out.json"), "p.py", "exec")


def test_disagreements_must_be_testable_and_not_type_errors() -> None:
    inputs = [(1,), (2,), (3,)]
    a = [("raises", "TypeError"), ("ok", "<object at 0x1>"), ("ok", "[]")]
    b = [("ok", "1"), ("ok", "2"), ("raises", "ValueError")]
    assert first_disagreement(inputs, a, b) == 2
    assert first_disagreement(inputs, a, a) is None


def test_answers_and_answer_tests() -> None:
    assert parse_answer("1") == 1 and parse_answer(" fix 2") == 2 and parse_answer("B") == 2
    assert parse_answer("maybe") is None and parse_answer(None) is None
    d = Disagreement(
        "src/shop/pages.py",
        "shop.pages",
        "paginate",
        ([], 0, 0),
        ("ok", "[]"),
        ("raises", "ValueError"),
    )
    assert "paginate([], 0, 0)" in d.question() and "raise ValueError (fix 2)" in d.question()
    ast.parse(answer_test(d, d.first))
    assert "assert paginate([], 0, 0) == []" in answer_test(d, d.first)
    assert "'ValueError'" in answer_test(d, d.second)


def test_the_answer_becomes_a_test_only_the_chosen_fix_passes(tmp_path: Path) -> None:
    repo = tour.prepare("001_paginate_off_by_one", tmp_path)
    cb = Answering("2")
    result = api.solve(
        repo, tour.task_text("001_paginate_off_by_one"), speed=Speed.SURE, cb=cb, client=_model()
    )
    assert result.status == "done", result.message
    (question,) = cb.asked
    assert "paginate(" in question and "raise ValueError (fix 2)" in question
    assert result.best is not None and result.best.id == "q1" and result.best.grade is Grade.A
    assert "raise ValueError" in result.best.diff and "test_qed_answer_paginate" in result.best.diff
    assert {c.id for c in result.candidates} >= {"c1", "c2", "q1"}


def test_nobody_to_ask_keeps_the_best_and_records_the_question(tmp_path: Path) -> None:
    repo = tour.prepare("001_paginate_off_by_one", tmp_path)
    result = api.solve(
        repo,
        tour.task_text("001_paginate_off_by_one"),
        speed=Speed.SURE,
        cb=Answering(None),
        client=_model(),
    )
    assert result.status == "done" and result.best is not None
    assert result.best.id in ("c1", "c2") and result.question and "paginate(" in result.question
    from qedcode.verify.receipt import load_receipt

    receipt = load_receipt(result.receipt_path)
    assert receipt.unverified[0].startswith("Open question:")
