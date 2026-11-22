"""Night Shift (AGENTS.md 15.13): a queue, unattended solves in quiet mode, one morning report."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from qedcode import tour
from qedcode.cli import app
from qedcode.engine import api
from qedcode.engine.night import NIGHT_DIR, Queue, latest_report, tasks_from_file
from qedcode.models.schemas import Speed
from qedcode.vcs.git import git

runner = CliRunner()


def test_the_queue_adds_without_duplicates_and_clears(tmp_path: Path) -> None:
    queue = Queue(tmp_path)
    assert queue.add(["fix a", "fix b", "fix a", "  "], Speed.FAST) == 2
    assert queue.add(["fix b", "fix c"], Speed.SURE) == 1
    assert [(t.request, t.speed) for t in queue.load()] == [
        ("fix a", "fast"),
        ("fix b", "fast"),
        ("fix c", "sure"),
    ]
    assert queue.clear() == 3 and queue.load() == []
    assert tasks_from_file("# comment\nfix a\n\n  fix b  \n") == ["fix a", "fix b"]


def test_a_night_run_solves_the_queue_and_writes_an_escaped_report(tmp_path: Path) -> None:
    repo = tour.prepare("001_paginate_off_by_one", tmp_path)
    task = tour.task_text("001_paginate_off_by_one")
    Queue(repo).add([task, "<script>alert(1)</script> do nothing"], Speed.NORMAL)
    calls: list[str] = []
    model_for = {task: tour.scripted_model("001_paginate_off_by_one")}

    def solve_one(request: str, speed: Speed):
        calls.append(request)
        model = model_for.get(request) or tour.scripted_model("001_paginate_off_by_one")
        return api.solve(repo, request, speed=speed, client=model)

    from qedcode.engine.night import run_night

    run = run_night(repo, solve_one)
    assert calls[0] == task and len(run.cards) == 2
    assert Queue(repo).load() == []  # finished tasks leave the queue
    report = run.report_path.read_text(encoding="utf-8")
    assert run.report_path == latest_report(repo)
    assert "Night Shift report" in report and "nothing was applied" in report
    assert '<span class="seal A">A</span>' in report
    assert "Proof strength" in report and "qed apply t-" in report
    assert "<script>alert(1)</script>" not in report and "&lt;script&gt;" in report
    assert git(repo, "status", "--porcelain").stdout.strip() == ""  # the user's tree untouched


def test_a_stop_keeps_the_rest_queued(tmp_path: Path) -> None:
    from qedcode.engine.night import run_night

    Queue(tmp_path).add(["one", "two"], Speed.FAST)
    run = run_night(tmp_path, lambda r, s: (_ for _ in ()).throw(AssertionError), stop=lambda: True)
    assert run.stopped_early and run.cards == []
    assert len(Queue(tmp_path).load()) == 2
    assert "still queued" in run.report_path.read_text(encoding="utf-8")


def test_the_cli_queue_commands(tmp_path: Path, monkeypatch) -> None:
    repo = tour.prepare("007_sort_order", tmp_path)
    monkeypatch.chdir(repo)
    added = runner.invoke(app, ["night", "add", "fix the order", "--speed", "fast"])
    assert added.exit_code == 0 and "Queued 1" in added.output
    tasks = repo / "tasks.txt"
    tasks.write_text("# mine\nsecond task\n")
    assert "Queued 1" in runner.invoke(app, ["night", "add", "--file", str(tasks)]).output
    listing = runner.invoke(app, ["night"]).output
    assert "1. [fast] fix the order" in listing and "2. [normal] second task" in listing
    empty = runner.invoke(app, ["night", "add"])
    assert empty.exit_code == 2
    assert "Removed 2" in runner.invoke(app, ["night", "clear"]).output
    none = runner.invoke(app, ["night", "report"])
    assert none.exit_code == 2 and "no Night Shift report" in none.output
    assert not (repo / NIGHT_DIR / "report").exists()


def test_failing_tests_become_tasks(tmp_path: Path, monkeypatch) -> None:
    repo = tour.prepare("007_sort_order", tmp_path)
    (repo / "tests" / "test_extra.py").write_text(
        "from board.scores import top_scores\n\n\ndef test_best_first():\n"
        "    assert top_scores([1, 9], 1) == [9]\n"
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "a failing test", identity=True)
    monkeypatch.chdir(repo)
    result = runner.invoke(app, ["night", "add", "--failing"])
    assert result.exit_code == 0, result.output
    (queued,) = Queue(repo).load()
    assert (
        queued.request.startswith("Make the failing test") and "test_best_first" in queued.request
    )
