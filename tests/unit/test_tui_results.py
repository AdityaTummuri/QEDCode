from __future__ import annotations

from pathlib import Path

from qedcode.models.schemas import Candidate, Grade, SolveResult, TaskSpec
from qedcode.runtime.fake import FakeModel
from qedcode.tui.screens import HELP_TEXT
from qedcode.tui.widgets import ACCENT, result_text
from tests.tui_helpers import make_app, ready, run_task, screen_name
from tests.zoo_helpers import script_for

ZOO = "001_paginate_off_by_one"
BROKEN = (
    "EDIT search_replace path=src/shop/pages.py\n<<<<<<< SEARCH\n    end = start + size - 1\n"
    "=======\n    end = start + size -\n>>>>>>> REPLACE\n"
)


def candidate(cid: str, grade: Grade | None) -> Candidate:
    return Candidate(id=cid, role="coder", grade=grade)


def done(grade: Grade) -> SolveResult:
    return SolveResult(
        status="done",
        task=TaskSpec(id="t-1", request="x"),
        best=candidate("c1", grade),
        message=f"Grade {grade.value} · 5/5 tests pass",
        explanation="It was off by one.",
    )


# ------------------------------------------------------------------ the result panel text


def test_only_grade_a_gets_the_accent_colour() -> None:
    a = result_text(done(Grade.A))
    assert a.plain.startswith("Grade A\n") and a.spans[0].style == ACCENT
    for grade in (Grade.B, Grade.C, Grade.D):
        text = result_text(done(grade))
        assert text.spans[0].style == "bold" and "green" not in str(text.spans[0].style)
    assert "It was off by one." in a.plain and "[a] apply" in a.plain and "[r] receipt" in a.plain


def test_a_result_without_a_fix_shows_the_message_and_the_question() -> None:
    result = SolveResult(
        status="no_fix",
        task=TaskSpec(id="t-1", request="x"),
        message="No fix passed the checks.",
        question="What should it return for an empty list?",
    )
    text = result_text(result).plain
    assert "No fix passed the checks." in text and "Question for you: What should it return" in text
    assert "[h] try harder" in text and "[a] apply" not in text


# ------------------------------------------------------------------ cards and panel on screen


async def test_cards_and_result_panel_fill_in_as_the_tries_come_back(tmp_path: Path) -> None:
    script = script_for(ZOO)
    good = script["coder"][0]
    script["coder"] = [BROKEN, BROKEN, good]
    app, _, _ = make_app(tmp_path, model=FakeModel(script))
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        await run_task(pilot, app)
        assert (
            str(app.query_one("#cards").render()) == "c1 failed (parse) | c2 failed (parse) | c3 A"
        )
        panel = str(app.query_one("#result").render())
        assert "Grade A" in panel and "repro test failed before, passes now" in panel
        assert "Changed src/shop/pages.py" in panel
        assert "[a] apply" in panel


async def test_a_new_task_clears_the_previous_cards_and_result(tmp_path: Path) -> None:
    app, model, _ = make_app(tmp_path, model=FakeModel(script_for(ZOO)))
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        await run_task(pilot, app)
        assert "c1 A" in str(app.query_one("#cards").render())
        model.script["intake"] = ["nonsense", "nonsense"]  # the next task will not go well
        app.last_result = None
        await pilot.click("#task")
        await pilot.press(*"another task")
        await pilot.press("enter")
        await pilot.pause()
        assert app.state in ("busy", "ready")


# ------------------------------------------------------------------ d, e, r and ?


async def test_d_shows_the_full_diff_and_escape_closes_it(tmp_path: Path) -> None:
    app, _, _ = make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        await run_task(pilot, app)
        await pilot.press("d")
        assert screen_name(app) == "DiffScreen"
        diff = app.screen.diff
        assert "src/shop/pages.py" in diff and "+    end = start + size\n" in diff
        assert "-    end = start + size - 1\n" in diff and "tests/test_qed_" in diff
        await pilot.press("escape")
        assert screen_name(app) != "DiffScreen"


async def test_e_explains_the_change_in_plain_words(tmp_path: Path) -> None:
    app, _, _ = make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        await run_task(pilot, app)
        await pilot.press("e")
        assert screen_name(app) == "TextScreen"
        assert app.screen.body.startswith("Changed src/shop/pages.py")
        await pilot.press("q")  # q closes a text screen instead of quitting the app
        assert screen_name(app) != "TextScreen" and app.is_running


async def test_r_shows_the_receipt_path_the_checks_and_the_gaps(tmp_path: Path) -> None:
    app, _, _ = make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        await run_task(pilot, app)
        await pilot.press("r")
        assert screen_name(app) == "TextScreen"
        body = app.screen.body
        assert app.last_result.receipt_path in body
        assert body.startswith("Grade A · repro test failed before, passes now")
        assert "fbpa: ok" in body and "targeted: ok" in body and "full: ok" in body
        assert "Not proven (plain English):" in body and "without network blocking" in body
        assert f"qed verify {app.last_result.receipt_path}" in body


async def test_question_mark_shows_the_help_with_every_key(tmp_path: Path) -> None:
    app, _, _ = make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        await pilot.press("escape")  # leave the task box: while it is focused "?" is just text
        await pilot.press("question_mark")
        assert screen_name(app) == "TextScreen" and app.screen.body == HELP_TEXT
        for key_line in (
            "a       apply",
            "d       full-screen diff",
            "h       try harder",
            "u       undo",
            "r       receipt",
            "m       change the speed",
            "Esc     cancel",
        ):
            assert key_line in HELP_TEXT
        await pilot.press("escape")


async def test_keys_without_a_result_give_a_gentle_hint(tmp_path: Path) -> None:
    app, _, _ = make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        await pilot.click("#log")
        for key in ("d", "e", "r", "a"):
            await pilot.press(key)
        assert app.log_lines.count("There is no result yet. Run a task first.") == 4
        assert screen_name(app) not in ("DiffScreen", "TextScreen")
