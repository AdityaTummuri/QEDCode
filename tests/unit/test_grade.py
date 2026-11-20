from __future__ import annotations

import pytest

from qedcode.models.schemas import Baseline, Candidate, CheckResult, Grade
from qedcode.verify.grade import (
    assign_grade,
    c_has_protected_changes,
    grade,
    needed_risk_approval,
    passed,
)
from qedcode.verify.guard import PROTECTED_MARKER

BASE = Baseline(commit="abc")


def ck(stage: str, ok: bool = True, **kw) -> CheckResult:
    return CheckResult(stage=stage, passed=ok, **kw)  # type: ignore[arg-type]


def cand(*checks: CheckResult, tests: int = 0, full: bool = False, failed: str | None = None):
    return Candidate(
        id="c1",
        role="coder",
        checks=list(checks),
        tests_passed=tests,
        full_suite_ran=full,
        failed_stage=failed,  # type: ignore[arg-type]
    )


def test_rejected_candidates_have_no_grade() -> None:
    c = cand(ck("apply"), ck("parse", False), failed="parse", tests=5, full=True)
    assert grade(c, BASE) is None


def test_d_nothing_could_be_checked() -> None:
    assert grade(cand(ck("apply"), ck("scope"), ck("risk")), BASE) is Grade.D


def test_c_checked_but_not_tested() -> None:
    assert grade(cand(ck("parse")), BASE) is Grade.C
    assert grade(cand(ck("lint")), BASE) is Grade.C  # lint alone counts as a static check


def test_b_tests_pass_but_no_proof() -> None:
    assert grade(cand(ck("parse"), ck("targeted"), tests=3), BASE) is Grade.B
    assert grade(cand(ck("parse"), ck("targeted"), tests=3, full=True), BASE) is Grade.B  # no fbpa


def test_b_when_fbpa_passed_but_the_full_suite_did_not_run() -> None:
    assert (
        grade(cand(ck("parse"), ck("fbpa"), ck("targeted"), tests=3, full=False), BASE) is Grade.B
    )


def test_a_needs_fbpa_and_the_full_suite() -> None:
    c = cand(ck("parse"), ck("fbpa"), ck("targeted"), ck("full"), tests=10, full=True)
    assert grade(c, BASE) is Grade.A


def test_tests_without_any_static_check_still_grade_b() -> None:
    assert grade(cand(ck("targeted"), tests=2), BASE) is Grade.B


def test_a_is_capped_at_b_when_a_risky_pattern_needed_approval() -> None:
    risk = ck("risk", True, needs_approval=True)
    c = cand(ck("parse"), risk, ck("fbpa"), ck("full"), tests=10, full=True)
    assert needed_risk_approval(c) and grade(c, BASE) is Grade.B


def test_a_is_capped_at_b_when_protected_files_changed() -> None:
    scope = ck("scope", True, details=f"{PROTECTED_MARKER} tests/test_x.py")
    c = cand(ck("parse"), scope, ck("fbpa"), ck("full"), tests=10, full=True)
    assert c_has_protected_changes(c) and grade(c, BASE) is Grade.B


def test_a_scope_check_without_the_marker_does_not_cap_the_grade() -> None:
    scope = ck("scope", True, details="nothing special")
    c = cand(ck("parse"), scope, ck("fbpa"), ck("full"), tests=10, full=True)
    assert not c_has_protected_changes(c) and grade(c, BASE) is Grade.A


def test_a_failed_check_does_not_count_as_passed() -> None:
    c = cand(ck("parse"), ck("fbpa", False), tests=4, full=True)
    assert not passed(c, "fbpa") and passed(c, "parse") and not passed(c, "lint")
    assert grade(c, BASE) is Grade.B


@pytest.mark.parametrize("risk_ok", [True, False])
def test_risk_without_needs_approval_never_caps(risk_ok: bool) -> None:
    c = cand(ck("parse"), ck("risk", risk_ok), ck("fbpa"), ck("full"), tests=10, full=True)
    assert not needed_risk_approval(c) and grade(c, BASE) is Grade.A


def test_assign_grade_is_the_only_writer() -> None:
    c = cand(ck("parse"), ck("targeted"), tests=2)
    assert c.grade is None
    assert assign_grade(c, BASE) is Grade.B and c.grade is Grade.B
    c.failed_stage = "lint"
    assert assign_grade(c, BASE) is None and c.grade is None


def test_no_other_module_assigns_a_grade() -> None:
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src" / "qedcode"
    offenders = []
    for path in src.rglob("*.py"):
        if path.name == "grade.py" or path.name == "schemas.py":
            continue
        text = path.read_text(encoding="utf-8")
        if ".grade =" in text or "grade=Grade" in text or ".grade=" in text:
            offenders.append(str(path.relative_to(src)))
    assert offenders == []


def test_a_change_that_alters_no_code_is_capped_at_c() -> None:
    c = cand(ck("parse"), ck("fbpa"), ck("targeted"), tests=5, full=True)
    c.code_changed = False
    assert grade(c, BASE) is Grade.C
