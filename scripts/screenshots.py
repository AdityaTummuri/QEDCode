"""Regenerate the README and docs screenshots from the real terminal UI (no GPU, no network).

The release workflow runs this, so no image is ever kept up to date by hand. It drives the Textual
app headlessly on the `qed try` demo project with its scripted model and saves SVG screenshots:

    uv run python scripts/screenshots.py [--out docs/assets]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path

SHOTS = {
    "result-card.svg": None,  # the main screen after the task finished
    "diff-view.svg": "d",
    "receipt-view.svg": "r",
}
TASK_PROJECT = "001_paginate_off_by_one"
SIZE = (110, 34)


async def _wait(predicate, timeout: float = 120.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while not predicate():
        if asyncio.get_event_loop().time() > deadline:
            raise TimeoutError("the app did not reach the expected state")
        await asyncio.sleep(0.05)


async def capture(out: Path) -> list[Path]:
    """Run one scripted task in the TUI and save each screenshot into `out`."""
    from qedcode import tour
    from qedcode.config import GlobalConfig
    from qedcode.hardware.detect import HardwareInfo
    from qedcode.tui.app import QedApp

    workdir = Path(tempfile.mkdtemp(prefix="qedcode-shots-"))
    project = tour.prepare(TASK_PROJECT, workdir)
    model = tour.scripted_model(TASK_PROJECT)
    app = QedApp(
        project,
        config=GlobalConfig(),
        client_factory=lambda cfg: model,
        hardware=HardwareInfo(),  # neutral: never the build machine's own GPU
        startup_checks=False,
    )
    saved: list[Path] = []
    async with app.run_test(size=SIZE) as pilot:
        await _wait(lambda: app.state == "ready")
        await pilot.click("#task")
        await pilot.press(*tour.task_text(TASK_PROJECT))
        await pilot.press("enter")
        await _wait(lambda: app.last_result is not None and app.state == "ready")
        await pilot.pause()
        for name, key in SHOTS.items():
            if key:
                await pilot.press(key)
                await pilot.pause()
            out.mkdir(parents=True, exist_ok=True)
            app.save_screenshot(filename=name, path=str(out))
            saved.append(out / name)
            if key:
                await pilot.press("escape")
                await pilot.pause()
    return saved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="docs/assets", help="folder for the SVG files")
    args = parser.parse_args(argv)
    # never read or write the developer's real ~/.qedcode
    os.environ["QEDCODE_HOME"] = tempfile.mkdtemp(prefix="qedcode-shots-home-")
    for path in asyncio.run(capture(Path(args.out))):
        print(f"saved {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
