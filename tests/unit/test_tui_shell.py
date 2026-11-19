from __future__ import annotations

from pathlib import Path

from textual.widgets import Footer, Input, RichLog

from qedcode.config import GlobalConfig
from qedcode.models.schemas import Event, Mode, Speed
from qedcode.runtime.client import ModelError
from qedcode.runtime.fake import FakeModel
from qedcode.tui.app import event_line
from qedcode.tui.widgets import (
    CardState,
    cards_text,
    header_text,
    mode_bar_text,
)
from qedcode.vcs import worktree
from tests.tui_helpers import SlowModel, make_app, ready, run_task, screen_name, submit, wait_for
from tests.zoo_helpers import script_for

# ------------------------------------------------------------------ pure text functions


def test_header_text() -> None:
    text = header_text(
        "myproj", "llamacpp:qwen3.5-4b-q4km", "llamacpp", "RTX 4050, 3.1/6.0 GB", "ready"
    )
    assert text.plain == (
        "QEDCode — myproj\nllamacpp:qwen3.5-4b-q4km · llamacpp · RTX 4050, 3.1/6.0 GB    [ready]"
    )
    assert "no model" in header_text("p", "", "", "", "loading").plain


def test_mode_bar_marks_the_current_speed() -> None:
    assert mode_bar_text(Speed.NORMAL, Mode.LOCAL, "pytest (48)") == (
        "Speed: Fast [Normal] Sure        Mode: local        Tests: pytest (48)"
    )
    assert mode_bar_text(Speed.SURE, Mode.MIX, "pytest (3)").startswith("Speed: Fast Normal [Sure]")


def test_cards_text() -> None:
    cards = [
        CardState("c1", failed_stage="targeted"),
        CardState("c2", grade="B"),
        CardState("c3", grade="A"),
        CardState("r1"),
    ]
    assert cards_text(cards) == "c1 failed (targeted) | c2 B | c3 A | r1 ..."
    assert cards_text([]) == ""


def test_event_lines() -> None:
    assert event_line(Event(kind="stage", message="Finding files")) == "Finding files"
    assert event_line(Event(kind="check", message="c1 · lint: ok")) == "  c1 · lint: ok"
    assert event_line(Event(kind="warning", message="careful")) == "Warning: careful"
    assert event_line(Event(kind="model", message="tok")) is None


# ------------------------------------------------------------------ the screen


async def test_the_layout_shows_header_speed_line_log_input_and_footer(tmp_path: Path) -> None:
    app, _, repo = make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        header = str(app.query_one("#header").render())
        assert repo.name in header and "fake:scripted" in header and "[ready]" in header
        bar = str(app.query_one("#modebar").render())
        assert (
            "Speed: Fast [Normal] Sure" in bar and "Mode: local" in bar and "Tests: pytest" in bar
        )
        assert app.query_one("#log", RichLog) and app.query_one("#task", Input).has_focus
        assert app.query_one(Footer)
        keys = {binding.key for binding in app.BINDINGS}
        assert {"a", "d", "e", "h", "u", "r", "m", "s", "q", "escape", "question_mark"} <= keys
        await pilot.pause()


async def test_typing_a_task_shows_its_steps_live_and_the_screen_never_freezes(
    tmp_path: Path,
) -> None:
    app, model, _ = make_app(tmp_path, model=SlowModel(script_for("001_paginate_off_by_one")))
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        await submit(pilot, "paginate drops the last item")
        await wait_for(lambda: app.state == "busy")
        assert app.query_one("#task", Input).value == ""  # the box was cleared
        # while the engine works the screen still answers the keyboard
        await pilot.press("m")
        assert app.speed is Speed.SURE and app.state == "busy"
        await pilot.press("m", "m")
        assert app.speed is Speed.NORMAL
        await wait_for(lambda: app.last_result is not None)
        await ready(app)
    text = "\n".join(app.log_lines)
    assert "> paginate drops the last item" in text
    assert "Taking a snapshot of your project..." in text
    assert "Finding files ...... src/shop/pages.py" in text
    assert "Writing a test that shows the problem ... done (it fails now)" in text
    assert "c1 · fbpa:" in text and "c1: grade A" in text
    assert app.last_result.best.grade.value == "A"
    # the steps arrived in order
    assert text.index("Taking a snapshot") < text.index("Finding files") < text.index("c1: grade A")


async def test_escape_cancels_a_running_task_and_leaves_no_worktrees(tmp_path: Path) -> None:
    slow = SlowModel(script_for("001_paginate_off_by_one"))
    slow.delay = 0.6
    app, _, repo = make_app(tmp_path, model=slow)
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        await submit(pilot, "paginate drops the last item")
        await wait_for(lambda: app.state == "busy")
        await pilot.press("escape")
        assert app.cancel_flag.is_set()
        await wait_for(lambda: app.last_result is not None)
        await ready(app)
    assert app.last_result.status == "cancelled"
    text = "\n".join(app.log_lines)
    assert "Cancelling..." in text and "cancelled" in text
    assert worktree.list_worktrees(repo) == []


async def test_a_second_task_is_refused_while_one_is_running(tmp_path: Path) -> None:
    slow = SlowModel(script_for("001_paginate_off_by_one"))
    app, _, _ = make_app(tmp_path, model=slow)
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        await submit(pilot, "paginate drops the last item")
        await wait_for(lambda: app.state == "busy")
        await submit(pilot, "another thing")
        assert any("A task is already running" in line for line in app.log_lines)
        await pilot.press("escape")
        await wait_for(lambda: app.last_result is not None)
        await ready(app)


async def test_the_input_stays_usable_while_the_model_loads_and_the_task_waits_for_it(
    tmp_path: Path,
) -> None:
    import threading

    gate = threading.Event()
    repo_model = FakeModel(script_for("001_paginate_off_by_one"))

    def slow_factory(cfg: GlobalConfig) -> FakeModel:
        gate.wait(30)
        return repo_model

    from qedcode.tui.app import QedApp
    from tests.zoo_helpers import zoo_git_repo

    repo = zoo_git_repo(tmp_path)
    app = QedApp(repo, config=GlobalConfig(), client_factory=slow_factory, startup_checks=False)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_for(lambda: app.state == "loading")
        assert "[loading]" in str(app.query_one("#header").render())
        await submit(pilot, "paginate drops the last item")  # typing works while loading
        await wait_for(lambda: any("Waiting for the model to load" in ln for ln in app.log_lines))
        assert app.state == "busy"
        gate.set()
        await wait_for(lambda: app.last_result is not None)
        await ready(app)
    assert app.last_result.best.grade.value == "A"


async def test_a_model_that_cannot_load_is_explained_and_tasks_fail_politely(
    tmp_path: Path,
) -> None:
    from qedcode.tui.app import QedApp
    from tests.zoo_helpers import zoo_git_repo

    def broken(cfg: GlobalConfig):
        raise ModelError("The llama.cpp engine is not installed. Run `qed setup`.")

    app = QedApp(
        zoo_git_repo(tmp_path), config=GlobalConfig(), client_factory=broken, startup_checks=False
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        assert any("Run `qed setup`" in ln for ln in app.log_lines)
        await run_task(pilot, app)
    assert app.last_result.status == "error" and "Run `qed setup`" in app.last_result.message


async def test_slash_commands_and_unknown_commands(tmp_path: Path) -> None:
    app, _, _ = make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app)
        await submit(pilot, "/speed sure")
        assert app.speed is Speed.SURE and "Speed is now sure." in app.log_lines
        await submit(pilot, "/speed warp")
        assert any("Use /speed fast" in ln for ln in app.log_lines)
        await submit(pilot, "/nonsense")
        assert any("Unknown command /nonsense" in ln for ln in app.log_lines)
        await submit(pilot, "/help")
        assert screen_name(app) == "TextScreen"
        await pilot.press("escape")
        assert screen_name(app) != "TextScreen"
        await submit(pilot, "/models")
        assert any(ln.startswith("Using: fake:scripted") for ln in app.log_lines)
