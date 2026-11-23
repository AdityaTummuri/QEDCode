from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from qedcode.engine import api, select
from qedcode.engine.budget import SPEED_DIAL, guess_kind
from qedcode.engine.callbacks import NonInteractiveCallbacks
from qedcode.engine.lock import project_lock
from qedcode.engine.solve import read_last_result, solve
from qedcode.models.schemas import Candidate, CheckResult, Grade, Mode, Speed
from qedcode.runtime.fake import FakeModel
from qedcode.vcs import worktree
from qedcode.verify.verify import verify_receipt
from tests.conftest import run_git, tree_state, zoo_project_names
from tests.zoo_helpers import script_for, zoo_git_repo

FENCE = "```"
PAGES = "src/shop/pages.py"
ZOO = "001_paginate_off_by_one"


def sr(path: str, old: str, new: str) -> str:
    return (
        f"EDIT search_replace path={path}\n<<<<<<< SEARCH\n{old}\n=======\n{new}\n>>>>>>> REPLACE\n"
    )


TALKATIVE = replace(SPEED_DIAL[Speed.NORMAL], lean=False)  # intake, locator and explainer on
BROKEN_SYNTAX = sr(PAGES, "    end = start + size - 1", "    end = start + size -")
NO_FIX = sr(
    PAGES, '    """Return the items on a 1-based page of the given size."""', '    """A page."""'
)
EDITS_A_TEST = sr("tests/test_pages.py", "assert paginate([], 1, 10) == []", "assert True")


def run_solve(project: Path, model: FakeModel, speed=Speed.NORMAL, mode=Mode.LOCAL, cb=None, **kw):
    cb = cb or NonInteractiveCallbacks()
    return solve(project, "paginate drops the last item", speed, mode, cb, client=model, **kw), cb


def tags(model: FakeModel) -> list[str]:
    return [c.tag for c in model.calls]


# ------------------------------------------------------------------ the seven scenarios of the plan


def test_1_a_good_coder_reply_reaches_grade_a_at_normal_speed(tmp_path: Path) -> None:
    project = zoo_git_repo(tmp_path)
    model = FakeModel(script_for(ZOO))
    result, cb = run_solve(project, model)
    assert result.status == "done" and result.best.grade is Grade.A, result.message
    assert result.best.id == "c1" and [c.id for c in result.candidates] == ["c1"]
    assert result.message.startswith("Grade A · repro test failed before, passes now")
    assert result.explanation.startswith("Changed src/shop/pages.py")
    assert "Failed before the change, passing with it: test_full_page" in result.explanation
    assert tags(model) == ["test_writer", "coder"]  # AGENTS.md 15.19: no call without need
    assert result.task.kind.value == "bugfix"
    assert result.task.target_grade is Grade.A
    receipt = Path(result.receipt_path)
    assert receipt == project / ".qed" / "receipts" / f"{result.task.id}.json" and receipt.exists()
    assert verify_receipt(receipt, project).verified
    messages = [e.message for e in cb.events]
    assert any(m.startswith("Finding files") for m in messages)
    assert any(
        "Writing a test that shows the problem ... done (it fails now)" in m for m in messages
    )
    assert messages[-1] == result.message


def test_2_a_broken_first_reply_then_a_good_repair_reaches_grade_a(tmp_path: Path) -> None:
    project = zoo_git_repo(tmp_path)
    script = script_for(ZOO, coder=[BROKEN_SYNTAX, BROKEN_SYNTAX, NO_FIX])
    # the repairer sees the best near miss (c3 failed at fbpa, the latest stage) and fixes it
    fix = sr(
        PAGES,
        '    """A page."""',
        '    """Return the items on a 1-based page of the given size."""',
    ) + sr(PAGES, "    end = start + size - 1", "    end = start + size")
    script["repairer"] = [fix]
    model = FakeModel(script)
    result, _ = run_solve(project, model)
    assert result.status == "done" and result.best.grade is Grade.A, result.message
    assert (
        result.best.id == "r1" and result.best.role == "repairer" and result.best.parent_id == "c3"
    )
    assert [c.id for c in result.candidates] == ["c1", "c2", "c3", "r1"]
    assert [c.failed_stage for c in result.candidates[:3]] == ["parse", "parse", "fbpa"]
    repair_call = model.calls_for("repairer")[0]
    assert "The change failed at the fbpa check" in repair_call.prompt  # what went wrong
    assert "YOUR LAST CHANGE:" in repair_call.prompt and '"""A page."""' in repair_call.prompt


def test_3_the_coder_edits_a_test_file_it_is_rejected_at_scope_then_a_good_reply_wins(
    tmp_path: Path,
) -> None:
    project = zoo_git_repo(tmp_path)
    script = script_for(ZOO)
    good = script["coder"][0]
    script["coder"] = [EDITS_A_TEST, good]
    result, _ = run_solve(project, FakeModel(script))
    assert result.best.grade is Grade.A and result.best.id == "c2"
    assert result.candidates[0].failed_stage == "scope" and result.candidates[0].grade is None


def test_4_a_repro_test_that_passes_on_the_base_is_dropped_and_the_target_becomes_b(
    tmp_path: Path,
) -> None:
    project = zoo_git_repo(tmp_path)
    passing = f"EDIT create_file path=tests/x.py\n{FENCE}python\ndef test_ok():\n    assert True\n{FENCE}\nFAILS NOW BECAUSE: it does not\n"
    script = script_for(ZOO, test_writer=[passing, passing])
    model = FakeModel(script)
    result, cb = run_solve(project, model)
    assert result.status == "done" and result.best.grade is Grade.B
    assert result.task.target_grade is Grade.B
    assert tags(model).count("test_writer") == 2  # one new attempt, then it gave up on the repro
    assert any("proves nothing" in e.message and e.kind == "warning" for e in cb.events)
    assert all(not c.reply.edits or "test_qed" not in c.diff for c in result.candidates)
    assert "No test was shown to fail before the change" in " ".join(
        verify_receipt(result.receipt_path, project).receipt.unverified
    )


def test_5_every_reply_broken_means_no_fix_with_the_closest_attempt(tmp_path: Path) -> None:
    project = zoo_git_repo(tmp_path)
    # the third reply has no edits, so it also uses the one format re-ask (a fourth scripted reply)
    script = script_for(
        ZOO,
        coder=[BROKEN_SYNTAX, NO_FIX, "no edits at all", "still no edits at all"],
        repairer=[BROKEN_SYNTAX],
    )
    result, _ = run_solve(project, FakeModel(script))
    assert result.status == "no_fix" and result.best is None and result.receipt_path is None
    assert len(result.candidates) == 4  # three tries and one repair
    assert "closest attempt (c2) failed at fbpa" in result.message
    assert result.question  # a question for the human
    assert read_last_result(project)["best_id"] == "c2"


def test_6_fast_speed_one_try_no_test_writer_grade_b(tmp_path: Path) -> None:
    project = zoo_git_repo(tmp_path)
    script = script_for(ZOO, test_writer=None)  # a call to the test writer would raise
    model = FakeModel(script)
    result, _ = run_solve(project, model, speed=Speed.FAST)
    assert result.best.grade is Grade.B and result.task.target_grade is Grade.B
    assert tags(model) == ["coder"]
    assert [c.id for c in result.candidates] == ["c1"]
    assert not result.best.full_suite_ran
    assert model.calls_for("coder")[0].temperature == 0.2


def test_7_cancel_in_the_middle_gives_cancelled_and_leaves_no_worktrees(tmp_path: Path) -> None:
    project = zoo_git_repo(tmp_path)
    flag = {"stop": False}

    def stop_after_first_candidate(event) -> None:
        if event.kind == "candidate":
            flag["stop"] = True

    cb = NonInteractiveCallbacks(
        on_event=stop_after_first_candidate, cancelled=lambda: flag["stop"]
    )
    script = script_for(ZOO, coder=[NO_FIX, NO_FIX, NO_FIX])
    result, _ = run_solve(project, FakeModel(script), cb=cb)
    assert result.status == "cancelled" and "cancelled" in result.message
    assert worktree.list_worktrees(project) == []
    assert not (project / ".qed" / "lock").exists()
    assert run_git(project, "status", "--porcelain").strip() == ""


# ------------------------------------------------------------------ the whole zoo


@pytest.mark.parametrize("name", zoo_project_names())
def test_every_bug_zoo_script_reaches_grade_a_with_the_fake_model(
    tmp_path: Path, name: str
) -> None:
    project = zoo_git_repo(tmp_path, name)
    model = FakeModel(script_for(name))
    task = (project / "task.txt").read_text(encoding="utf-8").strip()
    result = solve(project, task, Speed.NORMAL, Mode.LOCAL, NonInteractiveCallbacks(), client=model)
    assert result.status == "done" and result.best.grade is Grade.A, (name, result.message)
    assert result.best.id == "c1" and "src/" in result.best.diff
    assert verify_receipt(result.receipt_path, project).verified


# ------------------------------------------------------------------ safety and plumbing


def test_the_users_files_are_never_touched_and_prompts_are_saved(tmp_path: Path) -> None:
    project = zoo_git_repo(tmp_path)
    before = tree_state(project)
    result, _ = run_solve(project, FakeModel(script_for(ZOO)))
    assert result.status == "done"
    assert tree_state(project) == before
    assert worktree.list_worktrees(project) == []
    runs = project / ".qed" / "runs" / result.task.id
    names = sorted(p.name for p in runs.iterdir() if p.suffix == ".txt")
    assert "01-test_writer-a1-prompt.txt" in names and any("coder-c1-reply" in n for n in names)
    assert (runs / "baseline.log").exists() or any(p.name.endswith(".log") for p in runs.iterdir())


def test_time_budget_stops_the_tries(tmp_path: Path) -> None:
    project = zoo_git_repo(tmp_path)
    script = script_for(ZOO, coder=[NO_FIX, NO_FIX, NO_FIX], repairer=[NO_FIX])
    model = FakeModel(script)
    zero = replace(SPEED_DIAL[Speed.NORMAL], time_budget_s=0.0)
    result, _ = run_solve(project, model, settings=zero)
    assert result.status == "no_fix" and result.candidates == []
    assert tags(model) == ["test_writer"]  # the coder was never asked


def test_a_second_job_in_the_same_project_is_refused(tmp_path: Path) -> None:
    project = zoo_git_repo(tmp_path)
    with project_lock(project, "t-other"):
        result, _ = run_solve(project, FakeModel(script_for(ZOO)))
    assert result.status == "error" and "already running" in result.message


def test_the_intake_question_is_asked_and_the_answer_used(tmp_path: Path) -> None:
    project = zoo_git_repo(tmp_path)
    question = "KIND: bugfix\nACCEPTANCE:\n- unclear\nQUESTION: Which function is broken?\n"
    answered = "KIND: bugfix\nACCEPTANCE:\n- paginate returns size items\nQUESTION: NONE\n"
    model = FakeModel(script_for(ZOO, intake=[question, answered]))
    asked: list[str] = []

    class Asking(NonInteractiveCallbacks):
        def ask(self, q: str) -> str | None:
            asked.append(q)
            return "paginate"

    result, _ = run_solve(project, model, cb=Asking(), settings=TALKATIVE)
    assert asked == ["Which function is broken?"]
    assert model.calls_for("intake")[1].prompt.count("paginate") >= 2
    assert result.task.acceptance == ["paginate returns size items"] and result.status == "done"


def test_an_unanswerable_question_continues_and_becomes_needs_input_when_nothing_works(
    tmp_path: Path,
) -> None:
    project = zoo_git_repo(tmp_path)
    question = "KIND: bugfix\nACCEPTANCE:\n- unclear\nQUESTION: Which function is broken?\n"
    script = script_for(ZOO, intake=[question], coder=[NO_FIX] * 3, repairer=[NO_FIX])
    # non-interactive: ask() returns None
    result, cb = run_solve(project, FakeModel(script), settings=TALKATIVE)
    assert result.status == "needs_input" and result.question == "Which function is broken?"
    assert any(e.kind == "warning" and "may be unclear" in e.message for e in cb.events)


def test_model_errors_become_an_error_result(tmp_path: Path) -> None:
    from qedcode.runtime.client import ModelError

    class Down(FakeModel):
        def chat(self, *args, **kwargs):
            raise ModelError("QEDCode could not reach the model server. Start it and try again.")

    project = zoo_git_repo(tmp_path)
    result, _ = run_solve(project, Down({}))
    assert result.status == "error" and "could not reach the model server" in result.message
    assert worktree.list_worktrees(project) == []
    assert not (project / ".qed" / "lock").exists()


# ------------------------------------------------------------------ the full suite on the finalist


def test_a_candidate_that_breaks_another_test_is_rejected_by_the_full_suite_and_the_next_one_wins(
    tmp_path: Path,
) -> None:
    project = zoo_git_repo(tmp_path, "002_discount_threshold")
    (project / "src" / "store" / "cart_view.py").write_text(
        "from store.pricing import cart_total\n\n\ndef label(prices):\n    return f'Total: {cart_total(prices)}'\n"
    )
    (project / "tests" / "test_indirect.py").write_text(
        "from store.cart_view import label\n\n\ndef test_label():\n    assert label([10, 20]) == 'Total: 30'\n"
    )
    run_git(project, "add", "-A")
    run_git(project, "commit", "-q", "-m", "an indirect user of cart_total")
    script = script_for("002")
    good = script["coder"][0]
    sneaky = good + sr(
        "src/store/pricing.py",
        "    return apply_discount(sum(prices))",
        "    return float(apply_discount(sum(prices)))",
    )
    script["coder"] = [sneaky, good]
    result = solve(
        project,
        "discount at 100",
        Speed.NORMAL,
        Mode.LOCAL,
        NonInteractiveCallbacks(),
        client=FakeModel(script),
    )
    assert result.status == "done" and result.best.id == "c2" and result.best.grade is Grade.A
    assert result.candidates[0].failed_stage == "full" and result.candidates[0].grade is None


# ------------------------------------------------------------------ mix mode and the helper


def stuck_script() -> dict:
    return script_for(ZOO, coder=[NO_FIX, NO_FIX, NO_FIX], repairer=[NO_FIX])


def helper_model() -> FakeModel:
    return FakeModel({"coder": [script_for(ZOO)["coder"][0]]}, name="fake:helper")


def test_the_helper_is_used_when_confirmed_and_recorded_in_the_receipt(tmp_path: Path) -> None:
    project = zoo_git_repo(tmp_path)
    helper = helper_model()
    cb = NonInteractiveCallbacks(confirm_answer=True)
    result = solve(
        project,
        "paginate drops the last item",
        Speed.NORMAL,
        Mode.MIX,
        cb,
        client=FakeModel(stuck_script()),
        helper_factory=lambda: helper,
    )
    assert result.status == "done" and result.best.id == "h1" and result.best.role == "helper"
    assert result.best.grade is Grade.A and result.best.model == "fake:helper"
    assert any(
        "helper model" in p and "code will be sent" not in p or "Parts of your code" in p
        for p in cb.prompts
    )
    receipt = verify_receipt(result.receipt_path, project).receipt
    assert (
        receipt.model_info["helper_used"] is True and receipt.model_info["helper"] == "fake:helper"
    )
    assert any("sent to the helper model (fake:helper)" in g for g in receipt.unverified)
    assert helper.calls[0].tag == "coder" and helper.calls[0].temperature == 0.2


def test_the_helper_is_skipped_when_not_confirmed_in_local_mode_and_at_fast_speed(
    tmp_path: Path,
) -> None:
    helper = helper_model()
    asked = NonInteractiveCallbacks(confirm_answer=False)
    result = solve(
        zoo_git_repo(tmp_path / "a"),
        "paginate drops the last item",
        Speed.NORMAL,
        Mode.MIX,
        asked,
        client=FakeModel(stuck_script()),
        helper_factory=lambda: helper,
    )
    assert result.status == "no_fix" and helper.calls == [] and asked.prompts
    local = solve(
        zoo_git_repo(tmp_path / "b"),
        "x",
        Speed.NORMAL,
        Mode.LOCAL,
        NonInteractiveCallbacks(confirm_answer=True),
        client=FakeModel(stuck_script()),
        helper_factory=lambda: helper,
    )
    assert local.status == "no_fix" and helper.calls == []
    fast = solve(
        zoo_git_repo(tmp_path / "c"),
        "x",
        Speed.FAST,
        Mode.MIX,
        NonInteractiveCallbacks(confirm_answer=True),
        client=FakeModel(script_for(ZOO, test_writer=None, coder=[BROKEN_SYNTAX])),
        helper_factory=lambda: helper,
    )
    assert fast.status == "no_fix" and helper.calls == []


def test_a_preapproved_helper_is_not_asked_about(tmp_path: Path) -> None:
    helper = helper_model()
    cb = NonInteractiveCallbacks(confirm_answer=False)
    result = solve(
        zoo_git_repo(tmp_path),
        "paginate drops the last item",
        Speed.NORMAL,
        Mode.MIX,
        cb,
        client=FakeModel(stuck_script()),
        helper_factory=lambda: helper,
        helper_preapproved=True,
    )
    assert result.best.id == "h1" and cb.prompts == []


def test_an_unavailable_helper_is_a_warning_not_a_crash(tmp_path: Path) -> None:
    from qedcode.runtime.client import ModelError

    def broken() -> FakeModel:
        raise ModelError("There is no API key for openrouter.")

    cb = NonInteractiveCallbacks(confirm_answer=True)
    result = solve(
        zoo_git_repo(tmp_path),
        "paginate drops the last item",
        Speed.NORMAL,
        Mode.MIX,
        cb,
        client=FakeModel(stuck_script()),
        helper_factory=broken,
    )
    assert result.status == "no_fix"
    assert any(e.kind == "warning" and "no API key" in e.message for e in cb.events)


# ------------------------------------------------------------------ try harder


def test_try_harder_starts_from_the_previous_best_candidate(tmp_path: Path) -> None:
    project = zoo_git_repo(tmp_path)
    first_script = script_for(ZOO, test_writer=None)
    first, _ = run_solve(project, FakeModel(first_script), speed=Speed.FAST)
    assert first.best.grade is Grade.B
    second_model = FakeModel(script_for(ZOO))
    second, cb = run_solve(project, second_model, speed=Speed.SURE, resume=first)
    assert second.status == "done" and second.best.grade is Grade.A
    assert any(
        c.id.startswith("p") for c in second.candidates
    )  # the earlier attempt was carried over
    assert any("Starting from the earlier attempt c1" in e.message for e in cb.events)


def test_try_harder_starts_over_when_the_files_changed(tmp_path: Path) -> None:
    project = zoo_git_repo(tmp_path)
    first, _ = run_solve(project, FakeModel(script_for(ZOO, test_writer=None)), speed=Speed.FAST)
    (project / "src" / "shop" / "pages.py").write_text(
        (project / "src" / "shop" / "pages.py").read_text() + "\nEXTRA = 1\n"
    )
    second, cb = run_solve(project, FakeModel(script_for(ZOO)), resume=first)
    assert not any(c.id.startswith("p") for c in second.candidates)
    assert any("changed since the last try" in e.message for e in cb.events)


# ------------------------------------------------------------------ apply and undo through the API


def test_apply_and_undo_a_finished_result(tmp_path: Path) -> None:
    project = zoo_git_repo(tmp_path)
    result, _ = run_solve(project, FakeModel(script_for(ZOO)))
    before = tree_state(project)
    outcome = api.apply_result(project, result.task.id)
    assert outcome.status == "applied" and outcome.grade is Grade.A
    assert (
        "src/shop/pages.py" in outcome.files
        and "tests/test_qed_paginate_drops_the_last_item.py" in outcome.files
    )
    assert "start + size\n" in (project / "src" / "shop" / "pages.py").read_text()
    assert api.status(project)["last"]["task_id"] == result.task.id
    undone = api.undo(project)
    assert undone.status == "undone" and tree_state(project) == before
    assert api.undo(project).status == "refused"  # nothing left to undo


def test_apply_defaults_to_the_last_result_and_refuses_when_files_changed(tmp_path: Path) -> None:
    project = zoo_git_repo(tmp_path)
    run_solve(project, FakeModel(script_for(ZOO)))
    (project / "src" / "shop" / "pages.py").write_text("# I edited this meanwhile\n")
    outcome = api.apply_result(project)
    assert outcome.status == "refused" and "src/shop/pages.py" in outcome.files


def test_apply_errors_are_plain(tmp_path: Path) -> None:
    project = zoo_git_repo(tmp_path)
    assert "no finished result" in api.apply_result(project).message
    assert "no saved result for task t-nope" in api.apply_result(project, "t-nope").message


def test_low_grades_need_confirmation_before_apply(tmp_path: Path) -> None:
    project = zoo_git_repo(tmp_path)
    result, _ = run_solve(project, FakeModel(script_for(ZOO)))
    import json

    path = Path(result.receipt_path)
    data = json.loads(path.read_text())
    data["grade"] = "C"
    path.write_text(json.dumps(data))
    needs = api.apply_result(project, result.task.id)
    assert needs.status == "needs_confirmation" and needs.grade is Grade.C
    assert api.apply_result(project, result.task.id, allow_low_grade=True).status == "applied"


def test_api_solve_with_apply_flag_applies_only_a_or_b(tmp_path: Path) -> None:
    from qedcode.config import GlobalConfig

    project = zoo_git_repo(tmp_path)
    result = api.solve(
        project,
        "paginate drops the last item",
        config=GlobalConfig(),
        client=FakeModel(script_for(ZOO)),
        apply=True,
    )
    assert result.status == "done" and "Applied to" in result.message
    assert "start + size\n" in (project / "src" / "shop" / "pages.py").read_text()


def test_api_solve_input_errors(tmp_path: Path) -> None:
    from qedcode.config import GlobalConfig

    cfg = GlobalConfig()
    assert "Say what you want done" in api.solve(tmp_path, "  ", config=cfg).message
    plain = tmp_path / "plain"
    plain.mkdir()
    assert "not a git repository" in api.solve(plain, "fix it", config=cfg).message
    from tests.conftest import make_repo

    empty = make_repo(tmp_path / "empty", {"a.py": "A = 1\n"}, commit=False)
    assert "no commits yet" in api.solve(empty, "fix it", config=cfg).message
    cfg.local.backend = "openai_compat"  # no base_url: make_client fails with a plain message
    result = api.solve(zoo_git_repo(tmp_path / "z"), "fix it", config=cfg)
    assert result.status == "error" and "base_url" in result.message


# ------------------------------------------------------------------ selection rules (pure)


def cand(
    cid: str, grade=None, fbpa=False, tests=0, lines=0, failed=None, parent=None, diff="", keys=()
) -> Candidate:
    checks = []
    if fbpa:
        checks.append(CheckResult(stage="fbpa", passed=True))
    if failed:
        from qedcode.models.schemas import TestResult

        checks.append(
            CheckResult(
                stage=failed,
                passed=False,
                tests=[TestResult(key=k, outcome="failed") for k in keys],
            )
        )
    return Candidate(
        id=cid,
        role="coder",
        grade=grade,
        tests_passed=tests,
        diff_lines=lines,
        failed_stage=failed,
        checks=checks,
        parent_id=parent,
        diff=diff,
    )


def test_best_orders_by_grade_proof_tests_size_then_age() -> None:
    assert select.best([]) is None and select.best([cand("c1", failed="lint")]) is None
    b_small = cand("c1", Grade.B, tests=5, lines=3)
    a = cand("c2", Grade.A, fbpa=True, tests=5, lines=50)
    assert select.best([b_small, a]) is a
    with_proof = cand("c3", Grade.B, fbpa=True, tests=1, lines=90)
    assert select.best([b_small, with_proof]) is with_proof  # FBPA beats more tests
    more_tests = cand("c4", Grade.B, tests=9, lines=90)
    assert select.best([b_small, more_tests]) is more_tests
    smaller = cand("c5", Grade.B, tests=5, lines=1)
    assert select.best([b_small, smaller]) is smaller
    twin = cand("c6", Grade.B, tests=5, lines=3)
    assert select.best([b_small, twin]) is b_small  # earlier wins a full tie


def test_near_miss_prefers_the_latest_stage_then_tests_then_size() -> None:
    early = cand("c1", failed="parse", tests=0, lines=1)
    late = cand("c2", failed="targeted", tests=2, lines=40)
    later_more_tests = cand("c3", failed="targeted", tests=5, lines=90)
    assert select.near_miss([early, late]) is late
    assert select.near_miss([early, late, later_more_tests]) is later_more_tests
    smaller = cand("c4", failed="targeted", tests=5, lines=10)
    assert select.near_miss([later_more_tests, smaller]) is smaller
    assert select.near_miss([cand("c1", Grade.B, tests=1)]) is None


def test_apply_failures_are_near_misses_only_as_a_last_resort() -> None:
    apply_fail = cand("c1", failed="apply")
    parse_fail = cand("c2", failed="parse")
    assert select.near_miss([apply_fail, parse_fail]) is parse_fail
    assert select.near_miss([apply_fail]) is apply_fail


def test_no_progress_rules() -> None:
    parent = cand("c1", failed="targeted", keys=["t::a"], diff="same")
    assert select.no_progress(cand("r1", failed="targeted", keys=["t::a"], diff="x"), parent)
    assert select.no_progress(cand("r1", failed="lint", diff="same"), parent)  # identical diff
    assert not select.no_progress(cand("r1", failed="targeted", keys=["t::b"], diff="y"), parent)
    assert not select.no_progress(cand("r1", failed="fbpa", keys=["t::a"], diff="y"), parent)
    assert not select.no_progress(cand("r1", Grade.B, tests=3, diff="y"), parent)  # it worked


def test_reached_and_promising() -> None:
    assert select.reached(cand("c", Grade.A), Grade.B) and not select.reached(
        cand("c", Grade.B), Grade.A
    )
    assert not select.reached(cand("c", failed="lint"), Grade.B)
    assert select.promising(cand("c", tests=2, fbpa=True), Grade.A)
    assert not select.promising(cand("c", tests=2), Grade.A)  # target A needs a proof
    assert select.promising(cand("c", tests=2), Grade.B)
    assert not select.promising(cand("c", tests=0, fbpa=True), Grade.A)
    assert not select.promising(cand("c", failed="full", tests=3, fbpa=True), Grade.A)


# ------------------------------------------------------------------ fewer calls (AGENTS.md 15.19)

REPRO = "tests/test_qed_paginate_drops_the_last_item.py"


def repro_reply(*asserts: str) -> str:
    body = "\n".join(f"    assert {a}" for a in asserts)
    return (
        f"EDIT create_file path={REPRO}\n{FENCE}python\nfrom shop.pages import paginate\n\n\n"
        f"def test_page():\n{body}\n{FENCE}\nFAILS NOW BECAUSE: it drops the last item\n"
    )


WRONG_TEST = repro_reply("paginate([1, 2, 3, 4], 1, 2) == [1, 2, 3]")  # no right fix passes this
RIGHT_TEST = repro_reply("paginate([1, 2, 3, 4], 1, 2) == [1, 2]")
WRONG_FIX = sr(PAGES, "    end = start + size - 1", "    end = start + size + 1")


def test_a_wrong_first_test_is_settled_by_a_second_one_and_capped_at_b(tmp_path: Path) -> None:
    project = zoo_git_repo(tmp_path)
    good = script_for(ZOO)["coder"][0]
    model = FakeModel(script_for(ZOO, test_writer=[WRONG_TEST, RIGHT_TEST], coder=[good, good]))
    result, cb = run_solve(project, model)
    assert result.status == "done" and result.best.id == "d1", result.message
    assert result.best.grade is Grade.B and result.best.disputed_repro
    assert tags(model) == ["test_writer", "coder", "coder", "test_writer"]
    first, second = model.calls_for("test_writer")
    assert second.temperature > first.temperature
    assert "== [1, 2, 3]" not in second.prompt and "end = start + size\n" not in second.prompt
    verified = verify_receipt(result.receipt_path, project)
    assert verified.verified, verified.mismatches
    assert any("Two generated tests disagreed" in g for g in verified.receipt.unverified)
    assert "Two generated tests disagreed" in result.explanation
    assert "== [1, 2, 3]" not in result.best.diff  # only the second test travels with the fix


def test_a_wrong_fix_stays_rejected_when_the_second_test_is_right_too(tmp_path: Path) -> None:
    project = zoo_git_repo(tmp_path)
    script = script_for(
        ZOO, test_writer=[RIGHT_TEST, RIGHT_TEST], coder=[WRONG_FIX] * 3, repairer=[WRONG_FIX]
    )
    result, _ = run_solve(project, FakeModel(script))
    assert result.status == "no_fix" and result.best is None


@pytest.mark.parametrize(
    "second", [repro_reply("paginate([], 1, 2) == []"), repro_reply("undefined_name == 1")]
)
def test_a_second_test_that_shows_nothing_changes_nothing(tmp_path: Path, second: str) -> None:
    project = zoo_git_repo(tmp_path)
    good = script_for(ZOO)["coder"][0]
    script = script_for(ZOO, test_writer=[WRONG_TEST, second], coder=[good] * 3, repairer=[NO_FIX])
    model = FakeModel(script)
    result, _ = run_solve(project, model)
    assert result.status == "no_fix"
    assert len(model.calls_for("test_writer")) == 2  # asked once, not again


def test_one_failed_try_is_not_a_dispute(tmp_path: Path) -> None:
    project = zoo_git_repo(tmp_path)
    good = script_for(ZOO)["coder"][0]
    model = FakeModel(script_for(ZOO, coder=[WRONG_FIX, good]))
    result, _ = run_solve(project, model)
    assert result.best.id == "c2" and result.best.grade is Grade.A
    assert len(model.calls_for("test_writer")) == 1


def test_the_receipt_says_where_the_time_went(tmp_path: Path) -> None:
    project = zoo_git_repo(tmp_path)
    result, _ = run_solve(project, FakeModel(script_for(ZOO)))
    timings = verify_receipt(result.receipt_path, project).receipt.timings
    assert {"model:test_writer", "model:coder", "baseline", "repro", "checks", "total"} <= set(
        timings
    )
    assert timings["total"] >= timings["checks"] > 0


def test_sure_speed_still_asks_everything(tmp_path: Path) -> None:
    project = zoo_git_repo(tmp_path)
    model = FakeModel(script_for(ZOO))
    result, _ = run_solve(project, model, settings=TALKATIVE)
    assert result.best.grade is Grade.A
    assert tags(model) == ["intake", "locator", "test_writer", "coder", "explainer"]
    assert not SPEED_DIAL[Speed.SURE].lean


def test_a_vague_request_still_goes_through_intake(tmp_path: Path) -> None:
    project = zoo_git_repo(tmp_path)
    model = FakeModel(script_for(ZOO))
    solve(project, "fix it", Speed.NORMAL, Mode.LOCAL, NonInteractiveCallbacks(), client=model)
    assert tags(model)[0] == "intake"


def test_an_error_while_the_baseline_runs_is_reported_and_cleaned_up(tmp_path: Path, monkeypatch):
    from qedcode.vcs.git import GitError

    def broken(*args, **kwargs):
        raise GitError("git could not make a worktree")

    monkeypatch.setattr("qedcode.engine.solve.baseline_run", broken)
    project = zoo_git_repo(tmp_path)
    result, _ = run_solve(project, FakeModel(script_for(ZOO)))
    assert result.status == "error" and "could not make a worktree" in result.message
    assert worktree.list_worktrees(project) == []


@pytest.mark.parametrize(
    ("request_text", "kind"),
    [
        ("paginate drops the last item", "bugfix"),
        ("Add a docstring to paginate in pages.py", "docs"),
        ("The README example crashes with KeyError", "bugfix"),
        ("Write tests for the cart module", "test"),
        ("Rename helper to paginate_all everywhere", "refactor"),
        ("Add CSV export to the report page", "bugfix"),
    ],
)
def test_the_kind_is_guessed_from_fixed_words(request_text: str, kind: str) -> None:
    assert guess_kind(request_text).value == kind
