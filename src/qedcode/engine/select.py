"""Choosing between candidates (AGENTS.md section 6). Pure functions; never calls the model."""

from __future__ import annotations

from qedcode.models.schemas import STAGE_ORDER, Candidate, Grade


def has_fbpa(c: Candidate) -> bool:
    """Did a fail-before-pass-after test pass for this candidate?"""
    return any(check.stage == "fbpa" and check.passed for check in c.checks)


def best(pool: list[Candidate]) -> Candidate | None:
    """The best graded candidate: grade, then FBPA proof, then tests passed, then smaller change,
    then the earlier one. None when no candidate has a grade."""
    graded = [(i, c) for i, c in enumerate(pool) if c.grade is not None]
    if not graded:
        return None
    return min(
        graded,
        key=lambda item: (
            -item[1].grade.rank(),  # type: ignore[union-attr]
            0 if has_fbpa(item[1]) else 1,
            -item[1].tests_passed,
            item[1].diff_lines,
            item[0],
        ),
    )[1]


def _stage_index(stage: str | None) -> int:
    return STAGE_ORDER.index(stage) if stage in STAGE_ORDER else -1


def near_miss(pool: list[Candidate]) -> Candidate | None:
    """The rejected candidate closest to working: the latest failed stage, then most tests passed,
    then fewer changed lines. Ones that failed at `apply` only qualify if nothing better exists."""
    rejected = [c for c in pool if c.grade is None and c.failed_stage is not None]
    if not rejected:
        return None
    later = [c for c in rejected if c.failed_stage != "apply"]
    candidates = later or rejected
    return min(
        candidates,
        key=lambda c: (-_stage_index(c.failed_stage), -c.tests_passed, c.diff_lines),
    )


def failing_test_keys(c: Candidate) -> set[str]:
    """Keys of the tests that failed in the check that rejected this candidate."""
    keys: set[str] = set()
    for check in c.checks:
        if check.stage == c.failed_stage and not check.passed:
            keys.update(t.key for t in check.tests if t.outcome in ("failed", "error"))
    return keys


def no_progress(new: Candidate, parent: Candidate) -> bool:
    """True when a repair got nowhere: same failed stage and failing tests, or the same diff."""
    if new.grade is not None:
        return False
    if new.diff and new.diff == parent.diff:
        return True
    return new.failed_stage == parent.failed_stage and failing_test_keys(new) == failing_test_keys(
        parent
    )


def reached(c: Candidate, target: Grade) -> bool:
    """Has this candidate a grade at or above the target?"""
    return c.grade is not None and c.grade.rank() >= target.rank()


def promising(c: Candidate, target: Grade) -> bool:
    """Passed everything but the (finalist-only) full suite, and would reach `target` with it.

    Target A needs a fail-before-pass-after proof; target B only needs the tests to pass.
    """
    if c.failed_stage is not None or c.tests_passed == 0:
        return False
    if target is Grade.A:
        return has_fbpa(c)
    return True
