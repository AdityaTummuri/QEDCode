from __future__ import annotations

import pytest

from qedcode.models.schemas import (
    STAGE_ORDER,
    Candidate,
    CheckResult,
    EditOp,
    Event,
    Grade,
    ParsedReply,
    Receipt,
    SolveResult,
    TaskSpec,
)


def test_grade_rank_ordering() -> None:
    assert [g.rank() for g in (Grade.A, Grade.B, Grade.C, Grade.D)] == [4, 3, 2, 1]
    assert sorted(Grade, key=Grade.rank, reverse=True) == [Grade.A, Grade.B, Grade.C, Grade.D]
    assert Grade.A.rank() > Grade.B.rank() > Grade.C.rank() > Grade.D.rank()


def test_stage_order_matches_the_brief() -> None:
    assert STAGE_ORDER == ["apply", "parse", "scope", "risk", "lint", "fbpa", "targeted", "full"]


def test_list_defaults_are_not_shared() -> None:
    a, b = ParsedReply(), ParsedReply()
    a.plan.append("x")
    assert b.plan == []
    c1, c2 = Candidate(id="c1", role="coder"), Candidate(id="c2", role="coder")
    c1.checks.append(CheckResult(stage="apply", passed=True))
    assert c2.checks == []


def test_candidate_defaults() -> None:
    c = Candidate(id="c1", role="coder")
    assert c.grade is None
    assert c.failed_stage is None
    assert c.tests_passed == 0
    assert c.full_suite_ran is False


def test_models_round_trip_through_json() -> None:
    spec = TaskSpec(id="t-20261120-153201-ab12", request="fix it")
    receipt = Receipt(
        qed_version="0.0.1",
        created_at="2026-11-20T00:00:00Z",
        project_name="p",
        task=spec,
        base_commit="abc",
        base_ref="HEAD",
        patch="diff",
        patch_sha256="00",
        checks=[CheckResult(stage="parse", passed=True)],
        grade=Grade.B,
    )
    assert Receipt.model_validate_json(receipt.model_dump_json()) == receipt
    result = SolveResult(status="done", task=spec)
    assert SolveResult.model_validate_json(result.model_dump_json()) == result


def test_invalid_values_are_rejected() -> None:
    with pytest.raises(ValueError):
        EditOp(op="delete_everything", path="a.py", code="")
    with pytest.raises(ValueError):
        Event(kind="nonsense", message="x")
    with pytest.raises(ValueError):
        CheckResult(stage="made_up", passed=True)
