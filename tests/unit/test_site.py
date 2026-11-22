"""The website build and the Fool the Grader grader (website/play/grader.py)."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from qedcode.models.schemas import Receipt
from tests.conftest import REPO_ROOT


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def site(tmp_path_factory) -> Path:
    build = _load(REPO_ROOT / "scripts" / "build_site.py", "build_site")
    return build.build(tmp_path_factory.mktemp("site") / "_site")


def test_the_site_has_every_page_and_generated_file(site: Path) -> None:
    for rel in (
        "index.html",
        "site.css",
        "site.js",
        "mark.svg",
        ".nojekyll",
        "receipt/index.html",
        "receipt/viewer.js",
        "receipt/receipt.schema.json",
        "receipt/sample.json",
        "play/index.html",
        "play/play.js",
        "play/worker.js",
        "play/grader.py",
        "play/levels.json",
        "play/py/manifest.json",
        "play/py/tomli_w.py",
        "play/py/qedcode/verify/grade.py",
    ):
        assert (site / rel).is_file(), rel
    sample = Receipt.model_validate_json((site / "receipt" / "sample.json").read_text())
    assert sample.grade.value == "A" and sample.mutation is not None
    schema = json.loads((site / "receipt" / "receipt.schema.json").read_text())
    assert "grade" in schema["required"] and "mutation" in schema["properties"]
    for rel in json.loads((site / "play" / "py" / "manifest.json").read_text()):
        assert (site / "play" / "py" / rel).is_file(), rel


def test_pages_never_inline_receipt_text_as_html() -> None:
    viewer = (REPO_ROOT / "website" / "receipt" / "viewer.js").read_text()
    play = (REPO_ROOT / "website" / "play" / "play.js").read_text()
    for source in (viewer, play):
        assert "innerHTML" not in source and "insertAdjacentHTML" not in source


def test_scripts_come_only_from_the_site_or_a_pinned_cdn() -> None:
    for page in (REPO_ROOT / "website").rglob("*"):
        if page.suffix not in (".html", ".js"):
            continue
        text = page.read_text()
        for url in re.findall(r"(?:src=|importScripts\()\"(https?://[^\"]+)\"", text):
            assert url.startswith("https://cdn.jsdelivr.net/pyodide/v0.28.3/"), (page.name, url)


def _grade(site: Path, level_index: int, edits_for) -> dict:
    """Run the grader in a fresh interpreter with exactly the browser's module set."""
    levels = json.loads((site / "play" / "levels.json").read_text())
    level = levels[level_index]
    payload = json.dumps({"level": level, "edits": edits_for(level)})
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(site / "play" / "py"), str(site / "play")]),
    }
    code = "import sys, grader; print('@@' + grader.main_json(sys.stdin.read()))"
    done = subprocess.run(
        [sys.executable, "-W", "ignore", "-c", code],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        cwd=site,
        check=False,
        timeout=240,
    )
    assert "@@" in done.stdout, done.stderr[-2000:]
    return json.loads(done.stdout.split("@@", 1)[1].strip().splitlines()[0])


def _fixed(level: dict) -> str:
    return level["files"][level["source"]].replace("end = start + size - 1", "end = start + size")


NEW_TEST = (
    "from shop.pages import paginate\n\n\ndef test_full_page():\n"
    "    assert paginate([1, 2, 3, 4, 5], 1, 2) == [1, 2]\n"
)


def test_a_real_fix_with_a_new_test_wins(site: Path) -> None:
    r = _grade(site, 0, lambda lv: {lv["source"]: _fixed(lv), "tests/test_new.py": NEW_TEST})
    assert r["grade"] == "A" and r["won"] and r["hidden"]["passed"]
    assert r["link"].startswith("https://adityatummuri.github.io/QEDCode/receipt/#r=")


def test_a_fix_without_a_test_is_b_with_the_cli_advice(site: Path) -> None:
    r = _grade(site, 0, lambda lv: {lv["source"]: _fixed(lv)})
    assert r["grade"] == "B" and not r["won"] and r["hidden"]["passed"]
    assert r["next_step"].startswith("Add a test that fails on")


def test_shortcuts_are_caught(site: Path) -> None:
    def skip(lv):
        tests = lv["files"][lv["tests"]]
        return {
            lv["tests"]: "import pytest\n"
            + tests.replace("def test_", '@pytest.mark.skip(reason="x")\ndef test_', 1)
        }

    r = _grade(site, 0, skip)
    assert r["grade"] == "C" and "tests weakened" in r["summary"]
    assert r["next_step"].startswith("Undo the change that weakens tests")
    hard = _grade(site, 0, lambda lv: lv["hardcode"])
    assert hard["grade"] == "A" and not hard["won"] and not hard["hidden"]["passed"]
    broken = _grade(site, 0, lambda lv: {lv["source"]: "def broken(:\n"})
    assert broken["status"] == "rejected" and broken["summary"].startswith("Rejected at parse")
