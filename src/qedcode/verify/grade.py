"""The ONLY place grades are set (AGENTS.md section 8, rule 3: no success without evidence).

A = a test failed before and passes now, the full suite shows no regressions, nothing protected
    changed and no risky pattern needed approval.
B = relevant tests pass with no regressions, but no fail-before-pass-after test or no full run.
C = static checks pass but no relevant test ran, or the change alters no code outside tests
    (only comments, formatting, or files no test uses such as docs), or (qed check) it weakens
    a test, so passing tests say nothing about it.
D = no check could run.
None = rejected.
"""

from __future__ import annotations

from qedcode.models.schemas import Baseline, Candidate, Grade, Stage
from qedcode.verify.guard import protected_from_checks, weakened_from_checks


def passed(c: Candidate, stage: Stage) -> bool:
    """Did a recorded check for `stage` pass? (Stages that did not run are not recorded.)"""
    return any(check.stage == stage and check.passed for check in c.checks)


def needed_risk_approval(c: Candidate) -> bool:
    """Did the risk stage find something that needed the user's approval?"""
    return any(check.stage == "risk" and check.needs_approval for check in c.checks)


def c_has_protected_changes(c: Candidate) -> bool:
    """Did the change touch protected files (allowed only when the task says so)?"""
    return bool(protected_from_checks(c.checks))


def c_has_weakened_tests(c: Candidate) -> bool:
    """Did `qed check` find a weakened test (added skip, removed asserts, always-true assert)?"""
    return bool(weakened_from_checks(c.checks))


def grade(c: Candidate, base: Baseline) -> Grade | None:
    """The evidence grade of a candidate; None means it was rejected."""
    if c.failed_stage is not None:
        return None  # rejected
    static_ok = passed(c, "parse") or passed(c, "lint")
    tests_ran = c.tests_passed > 0  # at least one relevant test ran and passed
    if not static_ok and not tests_ran:
        return Grade.D  # nothing could be checked
    if not tests_ran:
        return Grade.C  # checked, not tested
    if not c.code_changed:
        return Grade.C  # tests pass, but they would pass without the change too
    if c_has_weakened_tests(c):
        return Grade.C  # the tests were weakened, so their passing proves nothing
    g = Grade.A if passed(c, "fbpa") and c.full_suite_ran else Grade.B
    if g is Grade.A and (needed_risk_approval(c) or c_has_protected_changes(c)):
        g = Grade.B  # proof is weaker: cap at B
    return g


def assign_grade(c: Candidate, base: Baseline) -> Grade | None:
    """Compute the grade and store it on the candidate (the only code that writes `c.grade`)."""
    c.grade = grade(c, base)
    return c.grade


def reset_grade(c: Candidate) -> None:
    """Forget any earlier grade before a candidate is checked again."""
    c.grade = None
