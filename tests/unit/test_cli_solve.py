from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qedcode.cli import app
from qedcode.config import GlobalConfig, save_global
from qedcode.models.schemas import Grade, Mode, SolveResult, Speed
from qedcode.runtime.client import ModelError
from qedcode.runtime.fake import FakeModel
from tests.conftest import tree_state
from tests.zoo_helpers import script_for, zoo_git_repo

ZOO = "001_paginate_off_by_one"
TASK = "paginate drops the last item"
NO_FIX = (
    "EDIT search_replace path=src/shop/pages.py\n<<<<<<< SEARCH\n"
    '    """Return the items on a 1-based page of the given size."""\n=======\n'
    '    """A page."""\n>>>>>>> REPLACE\n'
)


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    repo = zoo_git_repo(tmp_path)
    monkeypatch.chdir(repo)
    return repo


def use_models(monkeypatch, main: FakeModel, helper: FakeModel | None = None) -> None:
    def fake_make_client(config, role="main"):
        if role == "helper":
            if helper is None:
                raise ModelError("No helper model is set.")
            return helper
        return main

    monkeypatch.setattr("qedcode.cli.make_client", fake_make_client)
    monkeypatch.setattr("qedcode.engine.api.make_client", fake_make_client)


def run(*args: str):
    return CliRunner().invoke(app, ["solve", *args])


def test_solve_prints_progress_grade_explanation_and_receipt(project, monkeypatch) -> None:
    model = FakeModel(script_for(ZOO))
    use_models(monkeypatch, model)
    result = run(TASK)
    assert result.exit_code == 0, result.output
    out = result.output
    assert "Taking a snapshot of your project..." in out
    assert "Finding files ...... src/shop/pages.py" in out
    assert "Writing a test that shows the problem ... done (it fails now)" in out
    assert "c1 · fbpa:" in out and "c1: grade A" in out
    assert "Grade A" in out and "repro test failed before, passes now" in out
    assert "paginate computed the end of each page" in out  # the plain-English explanation
    assert "Tries: c1: grade A" in out
    assert "Receipt:" in out and "/.qed/receipts/" in out.replace("\n", "")
    assert "Apply it with: qed apply t-" in out


def test_solve_does_not_touch_your_files_unless_you_apply(project, monkeypatch) -> None:
    use_models(monkeypatch, FakeModel(script_for(ZOO)))
    before = tree_state(project)
    assert run(TASK).exit_code == 0
    assert tree_state(project) == before


def test_solve_json_is_one_solve_result(project, monkeypatch) -> None:
    use_models(monkeypatch, FakeModel(script_for(ZOO)))
    result = run(TASK, "--json")
    assert result.exit_code == 0, result.output
    parsed = SolveResult.model_validate_json(result.stdout)  # all of stdout is the JSON
    assert parsed.status == "done" and parsed.best.grade is Grade.A and parsed.best.id == "c1"
    assert parsed.receipt_path and Path(parsed.receipt_path).exists()
    assert parsed.explanation.startswith("paginate computed")
    assert parsed.message.startswith("Grade A")
    assert json.loads(result.stdout)["task"]["request"] == TASK
    assert "Taking a snapshot" not in result.stdout  # progress is not mixed into the JSON


def test_solve_apply_applies_an_a_or_b_result(project, monkeypatch) -> None:
    use_models(monkeypatch, FakeModel(script_for(ZOO)))
    result = run(TASK, "--apply")
    assert result.exit_code == 0, result.output
    assert "Applied to" in result.output and "qed undo" in result.output
    assert "start + size\n" in (project / "src" / "shop" / "pages.py").read_text()
    assert "Apply it with:" not in result.output


def test_solve_speed_option_and_bad_speed(project, monkeypatch) -> None:
    model = FakeModel(script_for(ZOO, test_writer=None))
    use_models(monkeypatch, model)
    fast = run(TASK, "--speed", "fast")
    assert fast.exit_code == 0 and "Grade B" in fast.output
    assert [c.tag for c in model.calls].count("coder") == 1
    bad = run(TASK, "--speed", "warp")
    assert bad.exit_code == 2 and "fast, normal or sure" in bad.output


def test_the_configured_speed_is_the_default(project, monkeypatch) -> None:
    cfg = GlobalConfig()
    cfg.engine.speed = Speed.FAST
    save_global(cfg)
    model = FakeModel(script_for(ZOO, test_writer=None))
    use_models(monkeypatch, model)
    assert run(TASK).exit_code == 0 and "test_writer" not in [c.tag for c in model.calls]


def test_no_fix_exits_1_with_the_closest_attempt_and_a_question(project, monkeypatch) -> None:
    script = script_for(ZOO, coder=[NO_FIX] * 3, repairer=[NO_FIX])
    use_models(monkeypatch, FakeModel(script))
    result = run(TASK)
    assert result.exit_code == 1
    assert "No fix passed the checks" in result.output and "closest attempt" in result.output
    assert "Question for you:" in result.output


def test_a_model_that_cannot_be_reached_exits_2_with_a_plain_message(project, monkeypatch) -> None:
    def broken(config, role="main"):
        raise ModelError("The llama.cpp engine (b12549) is not installed. Run `qed setup`.")

    monkeypatch.setattr("qedcode.cli.make_client", broken)
    result = run(TASK)
    assert result.exit_code == 2 and "Run `qed setup`" in result.output
    assert "Traceback" not in result.output


def test_a_folder_that_is_not_a_git_repo_exits_2(tmp_path: Path, monkeypatch) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.chdir(plain)
    use_models(monkeypatch, FakeModel({}))
    result = run(TASK)
    assert result.exit_code == 2 and "not a git repository" in result.output


def test_an_internal_error_exits_3_and_is_logged_not_shown(project, monkeypatch) -> None:
    use_models(
        monkeypatch, FakeModel({})
    )  # no scripted replies: the FakeModel raises a bug-like error
    result = run(TASK)
    assert result.exit_code == 3
    assert "Something went wrong inside QEDCode" in result.output and "qedcode.log" in result.output
    assert "Traceback" not in result.output and "FakeModelError" not in result.output
    from qedcode.config import logs_dir

    assert "FakeModelError" in (logs_dir() / "qedcode.log").read_text(encoding="utf-8")


def test_the_model_is_unloaded_afterwards_when_configured(project, monkeypatch) -> None:
    model = FakeModel(script_for(ZOO))
    use_models(monkeypatch, model)
    run(TASK)
    assert model.unload_count == 1
    cfg = GlobalConfig()
    cfg.engine.unload_on_exit = False
    save_global(cfg)
    other = FakeModel(script_for(ZOO))
    use_models(monkeypatch, other)
    run(TASK)
    assert other.unload_count == 0


# ------------------------------------------------------------------ the helper in mix mode


def stuck() -> dict:
    return script_for(ZOO, coder=[NO_FIX] * 3, repairer=[NO_FIX])


def test_the_helper_is_skipped_when_nobody_confirms_it(project, monkeypatch) -> None:
    cfg = GlobalConfig()
    cfg.engine.mode = Mode.MIX
    cfg.helper.enabled = True
    cfg.helper.model = "some/model"  # ask_before_use stays True: a CLI run cannot confirm
    save_global(cfg)
    helper = FakeModel({"coder": [script_for(ZOO)["coder"][0]]}, name="fake:helper")
    use_models(monkeypatch, FakeModel(stuck()), helper)
    result = run(TASK)
    assert result.exit_code == 1 and helper.calls == []


def test_the_helper_is_used_when_the_config_pre_approves_it(project, monkeypatch) -> None:
    cfg = GlobalConfig()
    cfg.engine.mode = Mode.MIX
    cfg.helper.enabled = True
    cfg.helper.model = "some/model"
    cfg.helper.ask_before_use = False
    save_global(cfg)
    helper = FakeModel({"coder": [script_for(ZOO)["coder"][0]]}, name="fake:helper")
    use_models(monkeypatch, FakeModel(stuck()), helper)
    result = run(TASK, "--json")
    assert result.exit_code == 0, result.output
    parsed = SolveResult.model_validate_json(result.stdout)
    assert parsed.best.id == "h1" and parsed.best.role == "helper"
    assert helper.calls and helper.calls[0].tag == "coder"


# ------------------------------------------------------------------ qed apply / qed undo


def test_apply_and_undo_commands(project, monkeypatch) -> None:
    use_models(monkeypatch, FakeModel(script_for(ZOO)))
    solved = run(TASK, "--json")
    task_id = json.loads(solved.stdout)["task"]["id"]
    before = tree_state(project)
    applied = CliRunner().invoke(app, ["apply", task_id])
    assert applied.exit_code == 0 and "Applied to" in applied.output
    assert tree_state(project) != before
    undone = CliRunner().invoke(app, ["undo"])
    assert undone.exit_code == 0 and "Undone" in undone.output
    assert tree_state(project) == before
    again = CliRunner().invoke(app, ["undo"])
    assert again.exit_code == 1 and "nothing to undo" in again.output


def test_apply_without_a_result_and_after_the_files_changed(project, monkeypatch) -> None:
    nothing = CliRunner().invoke(app, ["apply"])
    assert nothing.exit_code == 2 and "no finished result" in nothing.output
    use_models(monkeypatch, FakeModel(script_for(ZOO)))
    run(TASK)
    (project / "src" / "shop" / "pages.py").write_text("# changed by me\n")
    refused = CliRunner().invoke(app, ["apply"])
    assert refused.exit_code == 1 and "left alone" in refused.output


def test_apply_asks_before_a_low_grade(project, monkeypatch) -> None:
    use_models(monkeypatch, FakeModel(script_for(ZOO)))
    solved = json.loads(run(TASK, "--json").stdout)
    receipt = Path(solved["receipt_path"])
    data = json.loads(receipt.read_text())
    data["grade"] = "C"
    receipt.write_text(json.dumps(data))
    declined = CliRunner().invoke(app, ["apply", solved["task"]["id"]], input="n\n")
    assert declined.exit_code == 1 and "grade C" in declined.output
    assert "start + size\n" not in (project / "src" / "shop" / "pages.py").read_text()
    accepted = CliRunner().invoke(app, ["apply", solved["task"]["id"]], input="y\n")
    assert accepted.exit_code == 0 and "Applied to" in accepted.output
    yes = CliRunner().invoke(app, ["undo"])
    assert yes.exit_code == 0
    forced = CliRunner().invoke(app, ["apply", solved["task"]["id"], "--yes"])
    assert forced.exit_code == 0
