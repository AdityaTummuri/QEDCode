"""The QEDCode Check GitHub Action: its comment, its fail-below rule and its safety rules."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest
import yaml

from tests.conftest import REPO_ROOT


def _load():
    spec = importlib.util.spec_from_file_location("report", REPO_ROOT / "action" / "report.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


report = _load()
LINK = "https://adityatummuri.github.io/QEDCode/receipt/#r=abc"
GRADED_B = {
    "status": "graded",
    "grade": "B",
    "summary": "Grade B · 10/10 tests pass · lint clean · no protected files changed",
    "next_step": "Add a test that fails on main and passes with this change.",
    "receipt_path": ".qed/receipts/t-1.json",
    "failing_tests": [],
    "notes": [],
}


def test_a_graded_comment_has_the_grade_the_next_step_and_the_proof() -> None:
    text = report.render_comment(GRADED_B, LINK, "https://github.com/o/r/actions/runs/1")
    assert text.startswith(report.MARKER)
    assert "### ∎ QEDCode: Grade B" in text and GRADED_B["summary"] in text
    assert "**To raise the grade:** Add a test that fails on main" in text
    assert f"[Open the proof in the Receipt Viewer]({LINK})" in text
    assert "qed verify" in text


def test_grade_a_has_no_next_step_and_failures_are_listed() -> None:
    a = dict(GRADED_B, grade="A", next_step="", summary="Grade A · ...")
    assert "To raise the grade" not in report.render_comment(a)
    rejected = {
        "status": "rejected",
        "message": "Rejected at parse: syntax error",
        "failing_tests": [f"t::{i}" for i in range(12)],
    }
    text = report.render_comment(rejected)
    assert "change rejected" in text and "- `t::0`" in text and "and 2 more" in text
    broken = report.render_comment({"status": "error", "message": "No base commit."})
    assert "could not run" in broken and "No base commit." in broken


def test_fail_below() -> None:
    assert report.below("C", "B") and not report.below("B", "B") and not report.below("A", "b")
    assert report.below(None, "D")  # rejected counts as lowest
    assert not report.below("D", "none") and not report.below("D", "")
    with pytest.raises(ValueError):
        report.below("A", "E")


def test_render_writes_the_files_and_the_outputs(tmp_path: Path, monkeypatch) -> None:
    result = tmp_path / "result.json"
    result.write_text(json.dumps(GRADED_B))
    link = tmp_path / "link.txt"
    link.write_text(LINK + "\n")
    outputs = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    code = report.main(
        [
            "render",
            "--result",
            str(result),
            "--link-file",
            str(link),
            "--fail-below",
            "A",
            "--out",
            str(tmp_path / "c"),
            "--pr",
            "7",
        ]
    )
    assert code == 0
    assert (tmp_path / "c" / "comment.md").read_text(encoding="utf-8").startswith(report.MARKER)
    assert json.loads((tmp_path / "c" / "pr.json").read_text(encoding="utf-8"))["number"] == "7"
    text = outputs.read_text(encoding="utf-8")
    assert "grade<<__QED__\nB\n" in text and "failed<<__QED__\ntrue\n" in text
    assert f"receipt-link<<__QED__\n{LINK}\n" in text


def test_a_link_to_another_site_is_dropped(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "r.json").write_text(json.dumps(GRADED_B))
    (tmp_path / "l.txt").write_text("https://evil.example/#r=abc")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "o.txt"))
    report.main(
        [
            "render",
            "--result",
            str(tmp_path / "r.json"),
            "--link-file",
            str(tmp_path / "l.txt"),
            "--out",
            str(tmp_path / "c"),
        ]
    )
    assert "evil.example" not in (tmp_path / "c" / "comment.md").read_text(encoding="utf-8")


def test_a_missing_result_still_gives_a_comment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "o.txt"))
    report.main(
        [
            "render",
            "--result",
            str(tmp_path / "nope.json"),
            "--out",
            str(tmp_path / "c"),
            "--fail-below",
            "B",
        ]
    )
    assert "could not run" in (tmp_path / "c" / "comment.md").read_text(encoding="utf-8")
    assert "failed<<__QED__\ntrue" in (tmp_path / "o.txt").read_text(encoding="utf-8")


def _post_dir(tmp_path: Path, body: str, number: str = "7") -> Path:
    folder = tmp_path / "c"
    folder.mkdir(parents=True)
    (folder / "comment.md").write_text(body)
    (folder / "pr.json").write_text(json.dumps({"number": number}))
    return folder


def test_post_updates_the_one_sticky_comment(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple[str, str, dict | None]] = []

    def api(method, path, token, body=None):
        calls.append((method, path, body))
        if path.endswith("/comments?per_page=100"):
            return [{"id": 1, "body": "other"}, {"id": 42, "body": report.MARKER + "\nold"}]
        return {}

    monkeypatch.setattr(report, "_api", api)
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    folder = _post_dir(tmp_path, report.MARKER + "\nnew")
    assert report.main(["post", "--dir", str(folder)]) == 0
    assert calls[-1][0] == "PATCH" and calls[-1][1].endswith("/comments/42")


def test_post_refuses_a_forged_comment_or_pr(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setattr(report, "_api", lambda *a, **k: pytest.fail("must not call the API"))
    assert report.main(["post", "--dir", str(_post_dir(tmp_path, "hello"))]) == 1
    folder = _post_dir(tmp_path / "x", report.MARKER, number="7; rm -rf /")
    assert report.main(["post", "--dir", str(folder)]) == 0  # not a number: nothing posted
    monkeypatch.setenv("QED_EXPECTED_HEAD_SHA", "aaa")
    monkeypatch.setattr(report, "_api", lambda m, p, t, b=None: {"head": {"sha": "bbb"}})
    assert report.main(["post", "--dir", str(_post_dir(tmp_path / "y", report.MARKER))]) == 1


def test_the_action_never_puts_pull_request_values_into_a_script() -> None:
    action = yaml.safe_load((REPO_ROOT / "action.yml").read_text(encoding="utf-8"))
    for step in action["runs"]["steps"]:
        assert "${{" not in step.get("run", ""), step.get("name")
    for step in action["runs"]["steps"]:
        uses = step.get("uses", "")
        if uses:
            assert re.search(r"@[0-9a-f]{40}$", uses), uses
    assert set(action["outputs"]) >= {"grade", "summary", "next-step", "receipt-link"}


def test_the_pre_commit_hook_fails_on_weakened_tests() -> None:
    (hook,) = yaml.safe_load((REPO_ROOT / ".pre-commit-hooks.yaml").read_text(encoding="utf-8"))
    assert hook["id"] == "qedcode-check" and hook["pass_filenames"] is False
    assert "qed check" in hook["entry"] and "--fail-below B" in hook["entry"]
