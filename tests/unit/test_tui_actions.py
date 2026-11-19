from __future__ import annotations

import json
import subprocess
from pathlib import Path

from qedcode.config import GlobalConfig, load_global
from qedcode.models.schemas import Grade, Mode, Speed
from qedcode.runtime.fake import FakeModel
from tests.conftest import BUGZOO_DIR, tree_state
from tests.tui_helpers import ZOO, make_app, ready, run_task, screen_name, submit, wait_for
from tests.zoo_helpers import script_for

PAGES = "src/shop/pages.py"
NO_FIX = (
    f"EDIT search_replace path={PAGES}\n<<<<<<< SEARCH\n"
    '    """Return the items on a 1-based page of the given size."""\n=======\n'
    '    """A page."""\n>>>>>>> REPLACE\n'
)
RISKY = (
    f"EDIT search_replace path={PAGES}\n<<<<<<< SEARCH\n    end = start + size - 1\n=======\n"
    "    import subprocess\n    end = start + size\n>>>>>>> REPLACE\n"
)


async def on_screen(app, name: str) -> None:
    await wait_for(lambda: screen_name(app) == name)


# ------------------------------------------------------------------ apply and undo


async def test_a_applies_the_result_and_u_undoes_it(tmp_path: Path) -> None:
    app, _, repo = make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        await run_task(pilot, app)
        before = tree_state(repo)
        await pilot.press("a")
        await wait_for(lambda: any(ln.startswith("Applied to") for ln in app.log_lines))
        assert "start + size\n" in (repo / "src" / "shop" / "pages.py").read_text()
        assert tree_state(repo) != before
        await pilot.press("u")
        await wait_for(lambda: any(ln.startswith("Undone") for ln in app.log_lines))
        assert tree_state(repo) == before


async def test_a_low_grade_asks_before_applying(tmp_path: Path) -> None:
    app, _, repo = make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        await run_task(pilot, app)
        receipt = Path(app.last_result.receipt_path)
        data = json.loads(receipt.read_text())
        data["grade"] = "C"
        receipt.write_text(json.dumps(data))
        app.last_result.best.grade = Grade.C
        before = tree_state(repo)
        await pilot.press("a")
        await on_screen(app, "ConfirmScreen")
        assert app.screen.title_text == "Grade C" and "no test proves it" in app.screen.prompt
        await pilot.press("n")
        await wait_for(lambda: screen_name(app) != "ConfirmScreen")
        assert tree_state(repo) == before  # said no: nothing changed
        await pilot.press("a")
        await on_screen(app, "ConfirmScreen")
        await pilot.press("y")
        await wait_for(lambda: any(ln.startswith("Applied to") for ln in app.log_lines))
        assert tree_state(repo) != before


async def test_apply_after_the_files_changed_is_refused_politely(tmp_path: Path) -> None:
    app, _, repo = make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        await run_task(pilot, app)
        (repo / "src" / "shop" / "pages.py").write_text("# edited meanwhile\n")
        await pilot.press("a")
        await wait_for(lambda: any("left alone" in ln for ln in app.log_lines))
        assert (repo / "src" / "shop" / "pages.py").read_text() == "# edited meanwhile\n"


# ------------------------------------------------------------------ speed and try harder


async def test_m_cycles_the_speed_and_the_bar_follows(tmp_path: Path) -> None:
    app, _, _ = make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        await pilot.press("escape")
        seen = []
        for _ in range(3):
            await pilot.press("m")
            seen.append(app.speed)
            assert f"[{app.speed.value.capitalize()}]" in str(app.query_one("#modebar").render())
        assert seen == [Speed.SURE, Speed.FAST, Speed.NORMAL]


async def test_h_runs_again_at_the_next_speed_starting_from_the_best_try(tmp_path: Path) -> None:
    base = script_for(ZOO)
    script = {
        tag: [base[tag][0], base[tag][0]] for tag in ("intake", "locator", "coder", "explainer")
    }
    script["test_writer"] = base["test_writer"]  # only the second (normal speed) run writes a test
    cfg = GlobalConfig()
    cfg.engine.speed = Speed.FAST
    app, model, _ = make_app(tmp_path, model=FakeModel(script), config=cfg)
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        await run_task(pilot, app)
        assert app.last_result.best.grade is Grade.B and app.speed is Speed.FAST
        first_task = app.last_result.task.id
        await pilot.press("h")
        assert app.speed is Speed.NORMAL
        await wait_for(lambda: app.last_result.task.id != first_task)
        await ready(app)
    assert any("Trying harder at normal speed" in ln for ln in app.log_lines)
    assert any("Starting from the earlier attempt c1" in ln for ln in app.log_lines)
    assert app.last_result.task.id != first_task and app.last_result.best.grade is Grade.A
    assert any(c.id.startswith("p") for c in app.last_result.candidates)
    assert [c.tag for c in model.calls].count("test_writer") == 1


async def test_h_without_a_previous_task_says_so(tmp_path: Path) -> None:
    app, _, _ = make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        await pilot.press("escape")
        await pilot.press("h")
        assert any("nothing to retry yet" in ln for ln in app.log_lines)
        assert app.speed is Speed.NORMAL


# ------------------------------------------------------------------ the three dialogs


async def test_risky_code_shows_the_lines_and_yes_lets_it_continue(tmp_path: Path) -> None:
    app, _, _ = make_app(tmp_path, model=FakeModel(script_for(ZOO, coder=[RISKY])))
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        await submit(pilot, "paginate drops the last item")
        await on_screen(app, "ConfirmScreen")
        assert app.screen.title_text == "Risky code"
        assert "subprocess in src/shop/pages.py" in app.screen.prompt
        assert "src/shop/pages.py:" in app.screen.prompt  # it shows the lines
        await pilot.press("y")
        await wait_for(lambda: app.last_result is not None)
        await ready(app)
    assert app.last_result.best.grade is Grade.B  # approved risk caps the grade at B


async def test_risky_code_that_you_reject_is_dropped(tmp_path: Path) -> None:
    good = script_for(ZOO)["coder"][0]
    app, _, _ = make_app(tmp_path, model=FakeModel(script_for(ZOO, coder=[RISKY, good])))
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        await submit(pilot, "paginate drops the last item")
        await on_screen(app, "ConfirmScreen")
        await pilot.press("n")
        await wait_for(lambda: app.last_result is not None)
        await ready(app)
    assert app.last_result.candidates[0].failed_stage == "risk"
    assert app.last_result.best.id == "c2" and app.last_result.best.grade is Grade.A


async def test_an_unclear_task_asks_a_question_and_uses_the_answer(tmp_path: Path) -> None:
    asked = "KIND: bugfix\nACCEPTANCE:\n- unclear\nQUESTION: Which function is broken?\n"
    answered = "KIND: bugfix\nACCEPTANCE:\n- paginate returns size items\nQUESTION: NONE\n"
    app, model, _ = make_app(tmp_path, model=FakeModel(script_for(ZOO, intake=[asked, answered])))
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        await submit(pilot, "something is off with pages")
        await on_screen(app, "AskScreen")
        assert app.screen.question == "Which function is broken?"
        await pilot.press(*"paginate")
        await pilot.press("enter")
        await wait_for(lambda: app.last_result is not None)
        await ready(app)
    assert app.last_result.task.acceptance == ["paginate returns size items"]
    assert "paginate" in model.calls_for("intake")[1].prompt


async def test_skipping_the_question_carries_on_without_an_answer(tmp_path: Path) -> None:
    asked = "KIND: bugfix\nACCEPTANCE:\n- unclear\nQUESTION: Which function is broken?\n"
    app, model, _ = make_app(tmp_path, model=FakeModel(script_for(ZOO, intake=[asked])))
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        await submit(pilot, "something is off with pages")
        await on_screen(app, "AskScreen")
        await pilot.press("escape")
        await wait_for(lambda: app.last_result is not None)
        await ready(app)
    assert len(model.calls_for("intake")) == 1
    assert app.last_result.status == "done"
    assert any("may be unclear" in ln for ln in app.log_lines)


async def test_the_helper_dialog_says_code_will_leave_the_computer(
    tmp_path: Path, monkeypatch
) -> None:
    helper = FakeModel({"coder": [script_for(ZOO)["coder"][0]]}, name="fake:helper")
    monkeypatch.setattr("qedcode.engine.api.make_client", lambda cfg, role="main": helper)
    cfg = GlobalConfig()
    cfg.engine.mode = Mode.MIX
    cfg.helper.enabled = True
    cfg.helper.model = "some/model"
    stuck = script_for(ZOO, coder=[NO_FIX] * 3, repairer=[NO_FIX])
    app, _, _ = make_app(tmp_path, model=FakeModel(stuck), config=cfg)
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        await submit(pilot, "paginate drops the last item")
        await on_screen(app, "ConfirmScreen")
        assert app.screen.title_text == "Ask the helper model?"
        assert "Parts of your code will be sent to it" in app.screen.prompt
        await pilot.press("y")
        await wait_for(lambda: app.last_result is not None)
        await ready(app)
    assert app.last_result.best.id == "h1" and helper.calls


async def test_declining_the_helper_ends_with_no_fix(tmp_path: Path, monkeypatch) -> None:
    helper = FakeModel({"coder": [script_for(ZOO)["coder"][0]]}, name="fake:helper")
    monkeypatch.setattr("qedcode.engine.api.make_client", lambda cfg, role="main": helper)
    cfg = GlobalConfig()
    cfg.engine.mode = Mode.MIX
    cfg.helper.enabled = True
    cfg.helper.model = "some/model"
    stuck = script_for(ZOO, coder=[NO_FIX] * 3, repairer=[NO_FIX])
    app, _, _ = make_app(tmp_path, model=FakeModel(stuck), config=cfg)
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        await submit(pilot, "paginate drops the last item")
        await on_screen(app, "ConfirmScreen")
        await pilot.press("n")
        await wait_for(lambda: app.last_result is not None)
        await ready(app)
        panel = str(app.query_one("#result").render())
    assert app.last_result.status == "no_fix" and helper.calls == []
    assert "Question for you:" in panel


# ------------------------------------------------------------------ /check, /verify and settings


async def test_slash_check_grades_your_own_changes(tmp_path: Path) -> None:
    app, _, repo = make_app(tmp_path)
    patch = (BUGZOO_DIR / ZOO / "solution.patch").read_text(encoding="utf-8")
    subprocess.run(["git", "apply", "-"], cwd=repo, input=patch, text=True, check=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        await submit(pilot, "/check")
        await wait_for(lambda: any(ln.startswith("Grade B") for ln in app.log_lines))
        await ready(app)
        assert any(ln.startswith("Receipt:") for ln in app.log_lines)
        assert "Grade B" in str(app.query_one("#result").render())


async def test_slash_verify_reruns_a_receipt(tmp_path: Path) -> None:
    app, _, _ = make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        await run_task(pilot, app)
        await submit(pilot, f"/verify {app.last_result.receipt_path}")
        await wait_for(lambda: "VERIFIED" in app.log_lines)
        await submit(pilot, "/verify")
        assert any("Usage: /verify" in ln for ln in app.log_lines)


async def test_settings_screen_saves_mode_keep_alive_and_helper(tmp_path: Path) -> None:
    app, _, _ = make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        await pilot.press("escape")
        await pilot.press("s")
        await on_screen(app, "SettingsScreen")
        app.screen.query_one("#keep_alive").value = "7"
        app.screen.query_one("#helper").value = "openrouter/some-model"
        app.screen.query_one("#mode").value = "mix"
        await pilot.click("#save")
        await wait_for(lambda: screen_name(app) != "SettingsScreen")
        assert app.mode is Mode.MIX and "Mode: mix" in str(app.query_one("#modebar").render())
    saved = load_global()
    assert saved.engine.mode is Mode.MIX and saved.engine.keep_alive_minutes == 7
    assert saved.helper.model == "openrouter/some-model" and saved.helper.enabled is True
    assert any(ln.startswith("Settings saved.") for ln in app.log_lines)


async def test_cancelling_the_settings_changes_nothing(tmp_path: Path) -> None:
    app, _, _ = make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        await pilot.press("escape")
        await pilot.press("s")
        await on_screen(app, "SettingsScreen")
        await pilot.press("escape")
        await wait_for(lambda: screen_name(app) != "SettingsScreen")
        assert app.mode is Mode.LOCAL and app.config.engine.keep_alive_minutes == 10
