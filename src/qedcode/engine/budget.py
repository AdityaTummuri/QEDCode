"""The speed dial: every default number for how hard the engine tries (AGENTS.md section 6)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from qedcode.models.schemas import Grade, Speed, TaskKind


@dataclass(frozen=True)
class SpeedSettings:
    """One column of the speed dial table."""

    tries: int  # candidate tries (k)
    temperatures: tuple[float, ...]
    repair_rounds: int
    test_writer: Literal["off", "bugfix_feature", "unless_docs"]
    baseline_full: bool  # False = related tests only
    full_on_finalist: bool
    # Longest the whole suite may take before the change; slower suites are not run for the task
    # (a big repository's suite would use most of the time budget on every run). 0 = not run.
    full_suite_limit_s: int
    max_files: int  # files the locator may pick
    max_changed_lines: int  # per candidate
    helper: Literal["never", "after_repairs"]
    time_budget_s: float


SPEED_DIAL: dict[Speed, SpeedSettings] = {
    Speed.FAST: SpeedSettings(
        tries=1,
        temperatures=(0.2,),
        repair_rounds=0,
        test_writer="off",
        baseline_full=False,
        full_on_finalist=False,
        full_suite_limit_s=0,
        max_files=3,
        max_changed_lines=200,
        helper="never",
        time_budget_s=5 * 60,
    ),
    Speed.NORMAL: SpeedSettings(
        tries=3,
        temperatures=(0.2, 0.7, 0.9),
        repair_rounds=1,
        test_writer="bugfix_feature",
        baseline_full=True,
        full_on_finalist=True,
        full_suite_limit_s=120,
        max_files=5,
        max_changed_lines=300,
        helper="after_repairs",
        time_budget_s=15 * 60,
    ),
    Speed.SURE: SpeedSettings(
        tries=6,
        temperatures=(0.2, 0.6, 0.7, 0.8, 0.9, 1.0),
        repair_rounds=2,
        test_writer="unless_docs",
        baseline_full=True,
        full_on_finalist=True,
        full_suite_limit_s=300,
        max_files=8,
        max_changed_lines=600,
        helper="after_repairs",
        time_budget_s=30 * 60,
    ),
}

NEXT_SPEED = {Speed.FAST: Speed.NORMAL, Speed.NORMAL: Speed.SURE, Speed.SURE: Speed.SURE}


def settings_for(speed: Speed | str) -> SpeedSettings:
    """The settings for a speed (accepts the enum or its name)."""
    return SPEED_DIAL[Speed(speed)]


def max_changed_lines(speed: Speed | str) -> int:
    return settings_for(speed).max_changed_lines


def test_writer_on(speed: Speed | str, kind: TaskKind | str) -> bool:
    """Whether the test writer runs: off on fast, bugfix/feature on normal, not docs on sure."""
    mode = settings_for(speed).test_writer
    kind = TaskKind(kind)
    if mode == "off":
        return False
    if mode == "bugfix_feature":
        return kind in (TaskKind.BUGFIX, TaskKind.FEATURE)
    return kind is not TaskKind.DOCS


test_writer_on.__test__ = False  # type: ignore[attr-defined]  # not a pytest test


def target_grade(speed: Speed | str, repro_exists: bool) -> Grade:
    """fast: B. normal: A if a repro test exists, else B. sure: A."""
    speed = Speed(speed)
    if speed is Speed.FAST:
        return Grade.B
    if speed is Speed.NORMAL:
        return Grade.A if repro_exists else Grade.B
    return Grade.A


def temperatures_for(speed: Speed | str) -> list[float]:
    """The temperature of each try, in order, as many as there are tries."""
    s = settings_for(speed)
    return list(s.temperatures[: s.tries])


def next_speed(speed: Speed | str) -> Speed:
    """The speed 'try harder' moves to: fast -> normal -> sure (sure stays sure)."""
    return NEXT_SPEED[Speed(speed)]
