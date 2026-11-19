"""Helpers for driving the Textual app with its test pilot (FakeModel, real git repos)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path

from qedcode.config import GlobalConfig
from qedcode.runtime.fake import FakeModel
from qedcode.tui.app import QedApp
from tests.zoo_helpers import script_for, zoo_git_repo

ZOO = "001_paginate_off_by_one"
TASK = "paginate drops the last item"


class SlowModel(FakeModel):
    """A FakeModel that takes a while per call, so tests can act while a task is running."""

    delay: float = 0.4

    def chat(self, *args, **kwargs):
        time.sleep(self.delay)
        return super().chat(*args, **kwargs)


async def wait_for(
    predicate: Callable[[], bool], timeout: float = 90.0, step: float = 0.05
) -> None:
    """Wait (without blocking the app's event loop) until `predicate()` is true."""
    deadline = asyncio.get_event_loop().time() + timeout
    while not predicate():
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError("timed out waiting for the app")
        await asyncio.sleep(step)


def make_app(
    tmp_path: Path,
    *,
    model: FakeModel | None = None,
    config: GlobalConfig | None = None,
    name: str = ZOO,
    startup_checks: bool = False,
    **kwargs,
) -> tuple[QedApp, FakeModel, Path]:
    repo = zoo_git_repo(tmp_path, name)
    model = model or FakeModel(script_for(name))
    app = QedApp(
        repo,
        config=config or GlobalConfig(),
        client_factory=lambda cfg: model,
        startup_checks=startup_checks,
        **kwargs,
    )
    return app, model, repo


async def ready(app: QedApp) -> None:
    await wait_for(lambda: app.state == "ready")


async def submit(pilot, text: str) -> None:
    await pilot.click("#task")
    await pilot.press(*text)
    await pilot.press("enter")


async def run_task(pilot, app: QedApp, text: str = TASK) -> None:
    """Type a task, press Enter and wait until its result is in."""
    app.last_result = None
    await submit(pilot, text)
    await wait_for(lambda: app.last_result is not None)
    await wait_for(lambda: app.state == "ready")


def screen_name(app: QedApp) -> str:
    return type(app.screen).__name__
