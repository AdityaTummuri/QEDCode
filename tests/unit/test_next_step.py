"""receipt.next_step: one plain sentence on what would raise the grade, shared by every front door."""

from __future__ import annotations

from qedcode.models.schemas import CheckResult, Grade, Receipt, TaskSpec, TestResult
from qedcode.verify.receipt import (
    NETWORK_NOTE,
    NO_CODE_CHANGE_PREFIX,
    NOT_RUNNABLE_PREFIX,
    next_step,
)


def _receipt(grade: str, *, checks=None, fbpa=(), unverified=(), protected=()) -> Receipt:
    tests = [TestResult(key="tests/test_x.py::test_a", outcome="passed")]
    return Receipt(
        qed_version="0",
        created_at="2026-11-23T00:00:00Z",
        project_name="p",
        task=TaskSpec(id="t-1", request="r"),
        base_commit="abc",
        base_ref="main",
        patch="",
        patch_sha256="",
        checks=checks
        if checks is not None
        else [CheckResult(stage="full", passed=True, tests=tests)],
        fbpa_tests=list(fbpa),
        protected_changes=list(protected),
        grade=Grade(grade),
        unverified=[*unverified, NETWORK_NOTE],
    )


def test_grade_a_needs_nothing() -> None:
    assert next_step(_receipt("A", fbpa=["k"])) == ""


def test_a_b_without_a_repro_test_asks_for_one_naming_the_base() -> None:
    assert next_step(_receipt("B")).startswith("Add a test that fails on main")


def test_a_b_with_a_repro_but_no_full_suite() -> None:
    gap = "Full suite not run (fast speed)."
    assert "--speed normal" in next_step(_receipt("B", fbpa=["k"], unverified=[gap]))
    slow = "Full suite not run: the full suite took over 120 s, too slow for every task."
    assert "too slow" in next_step(_receipt("B", fbpa=["k"], unverified=[slow]))


def test_edited_tests_and_weakened_tests() -> None:
    assert "Leave existing tests" in next_step(_receipt("B", fbpa=["k"], protected=["tests/t.py"]))
    weakened = CheckResult(
        stage="scope",
        passed=True,
        details="Protected files changed: tests/t.py\nTests weakened: tests/t.py",
    )
    text = next_step(_receipt("C", checks=[weakened, *_receipt("B").checks]))
    assert text.startswith("Undo the change that weakens tests in tests/t.py")


def test_c_without_tests_and_untestable_changes() -> None:
    assert next_step(_receipt("C", checks=[])).startswith("Add a test that covers")
    inert = f"{NO_CODE_CHANGE_PREFIX} (only comments)."
    assert next_step(_receipt("C", unverified=[inert])).startswith("Nothing to prove")
    broken = f"{NOT_RUNNABLE_PREFIX} (no pytest)."
    assert "qed doctor" in next_step(_receipt("D", checks=[], unverified=[broken]))


def test_a_weakened_test_wins_over_no_code_change() -> None:
    weakened = CheckResult(
        stage="scope", passed=True, details="Protected files changed: t.py\nTests weakened: t.py"
    )
    inert = f"{NO_CODE_CHANGE_PREFIX} (only comments)."
    receipt = _receipt("C", checks=[weakened, *_receipt("B").checks], unverified=[inert])
    assert next_step(receipt).startswith("Undo the change that weakens tests in t.py")
