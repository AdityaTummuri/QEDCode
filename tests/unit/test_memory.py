"""Learns your project (AGENTS.md section 15.1)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from qedcode.cli import app
from qedcode.config import GlobalConfig
from qedcode.engine import api
from qedcode.models.schemas import Grade, Mode, Speed, TaskKind, TaskSpec
from qedcode.project.memory import (
    MAX_DIFF_CHARS,
    MAX_LESSONS,
    Memory,
    example_budget,
    fts_query,
    keywords,
    memory_path,
    render_examples,
)
from qedcode.runtime.fake import FakeModel
from tests.zoo_helpers import script_for, zoo_git_repo

ZOO = "001_paginate_off_by_one"


def make_receipt(task_id: str, request: str, patch: str, grade: Grade = Grade.A):
    from qedcode.models.schemas import Receipt

    return Receipt(
        qed_version="0.1.0",
        created_at="2026-11-20T00:00:00Z",
        project_name="p",
        task=TaskSpec(id=task_id, request=request, kind=TaskKind.BUGFIX, speed=Speed.NORMAL),
        base_commit="abc",
        base_ref="HEAD",
        patch=patch,
        patch_sha256="x",
        checks=[],
        grade=grade,
    )


def diff_for(path: str, body: str = "x = 1") -> str:
    return (
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-x = 0\n+{body}\n"
    )


def project_with(tmp_path: Path, *files: str) -> Path:
    for name in files:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x = 0\n")
    return tmp_path


# ------------------------------------------------------------------ keywords and the FTS query


def test_keywords_are_distinct_lowercase_words_of_three_or_more_characters() -> None:
    assert keywords("Fix the Paginate bug, paginate AGAIN in cart_total()") == [
        "fix",
        "the",
        "paginate",
        "bug",
        "again",
        "cart",
        "total",
    ]


def test_the_fts_query_cannot_inject_syntax(tmp_path: Path) -> None:
    nasty = 'x" OR rowid:1 NEAR(a b) * -foo ^bar AND NOT "'
    query = fts_query(nasty)
    assert query == '"rowid" OR "near" OR "foo" OR "bar" OR "and" OR "not"'
    root = project_with(tmp_path, "a.py")
    memory = Memory(root)
    memory.record(make_receipt("t-1", "fix rowid near foo", diff_for("a.py")))
    assert [lesson.task_id for lesson in memory.similar(nasty)] == ["t-1"]  # runs, no syntax error
    assert memory.similar("") == [] and memory.similar("!!! ??? 12") == []


# ------------------------------------------------------------------ recording


def test_only_grade_a_and_b_are_remembered_and_each_task_once(tmp_path: Path) -> None:
    root = project_with(tmp_path, "a.py")
    memory = Memory(root)
    assert memory.record(make_receipt("t-1", "fix a", diff_for("a.py"), Grade.A)) is True
    assert memory.record(make_receipt("t-1", "fix a again", diff_for("a.py"), Grade.A)) is False
    assert memory.record(make_receipt("t-2", "fix b", diff_for("a.py"), Grade.B)) is True
    assert memory.record(make_receipt("t-3", "fix c", diff_for("a.py"), Grade.C)) is False
    assert memory.record(make_receipt("t-4", "fix d", diff_for("a.py"), Grade.D)) is False
    assert memory.record(make_receipt("t-5", "fix e", "", Grade.A)) is False  # nothing changed
    assert [lesson.task_id for lesson in memory.lessons()] == ["t-2", "t-1"]
    assert memory.lessons()[1].request == "fix a"  # the first record is kept


def test_a_lesson_stores_the_request_files_and_a_capped_diff(tmp_path: Path) -> None:
    root = project_with(tmp_path, "pkg/a.py")
    huge = diff_for("pkg/a.py", "y = 1  # " + "z" * (MAX_DIFF_CHARS * 2))
    Memory(root).record(make_receipt("t-1", "make y one", huge))
    (lesson,) = Memory(root).lessons()
    assert lesson.files == ["pkg/a.py"] and lesson.grade == "A" and lesson.kind == "bugfix"
    assert len(lesson.diff) == MAX_DIFF_CHARS and lesson.request == "make y one"


def test_only_the_newest_lessons_are_kept(tmp_path: Path) -> None:
    root = project_with(tmp_path, "a.py")
    memory = Memory(root)
    for number in range(MAX_LESSONS + 5):
        memory.record(make_receipt(f"t-{number:04d}", f"task number {number}", diff_for("a.py")))
    lessons = memory.lessons()
    assert len(lessons) == MAX_LESSONS
    assert lessons[0].task_id == f"t-{MAX_LESSONS + 4:04d}" and lessons[-1].task_id == "t-0005"


# ------------------------------------------------------------------ finding similar fixes


def test_similar_ranks_by_relevance_and_skips_lessons_whose_files_are_gone(tmp_path: Path) -> None:
    root = project_with(tmp_path, "shop/cart.py", "shop/pages.py", "shop/gone.py")
    memory = Memory(root)
    memory.record(make_receipt("t-cart", "cart total ignores discount", diff_for("shop/cart.py")))
    memory.record(make_receipt("t-page", "paginate drops last item", diff_for("shop/pages.py")))
    memory.record(make_receipt("t-gone", "paginate also breaks", diff_for("shop/gone.py")))
    (root / "shop/gone.py").unlink()
    found = memory.similar("the paginate function drops an item")
    assert [lesson.task_id for lesson in found] == ["t-page"]
    assert memory.similar("cart discount", limit=1)[0].task_id == "t-cart"
    assert memory.similar("completely unrelated words here") == []


def test_matching_looks_at_file_names_and_the_diff_too(tmp_path: Path) -> None:
    root = project_with(tmp_path, "billing/invoice.py")
    Memory(root).record(
        make_receipt("t-1", "make it right", diff_for("billing/invoice.py", "rounding = True"))
    )
    assert Memory(root).similar("invoice problem")  # matched through the file path
    assert Memory(root).similar("rounding")  # matched through the diff


# ------------------------------------------------------------------ forget, clear, damage


def test_forget_and_clear(tmp_path: Path) -> None:
    root = project_with(tmp_path, "a.py")
    memory = Memory(root)
    memory.record(make_receipt("t-1", "alpha fix", diff_for("a.py")))
    memory.record(make_receipt("t-2", "beta fix", diff_for("a.py")))
    first, second = memory.lessons()[1], memory.lessons()[0]
    assert memory.forget(first.id) is True and memory.forget(first.id) is False
    assert memory.similar("alpha") == []  # also gone from the search index
    assert [lesson.task_id for lesson in memory.lessons()] == [second.task_id]
    assert memory.clear() == 1 and not memory_path(root).exists()
    assert memory.lessons() == [] and memory.clear() == 0


def test_a_damaged_database_is_ignored_and_clear_repairs_it(tmp_path: Path) -> None:
    root = project_with(tmp_path, "a.py")
    path = memory_path(root)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"this is not an sqlite database" * 50)
    memory = Memory(root)
    assert memory.lessons() == [] and memory.similar("anything here") == []
    assert memory.record(make_receipt("t-1", "x fix", diff_for("a.py"))) is False  # no crash
    memory.clear()
    assert memory.record(make_receipt("t-1", "x fix", diff_for("a.py"))) is True
    assert len(memory.lessons()) == 1


def test_a_database_without_the_search_table_is_repaired_in_place(tmp_path: Path) -> None:
    root = project_with(tmp_path, "a.py")
    path = memory_path(root)
    path.parent.mkdir(parents=True)
    sqlite3.connect(path).close()  # a valid but empty database
    assert Memory(root).record(make_receipt("t-1", "x fix", diff_for("a.py"))) is True


# ------------------------------------------------------------------ the prompt block


def test_the_example_budget_follows_the_context() -> None:
    assert example_budget(4096) == 0 and example_budget(5999) == 0
    assert example_budget(8192) == 1228 - 28  # 15% of 8192 = 1228, capped at 1200
    assert example_budget(6000) == 900 and example_budget(32768) == 1200


def test_render_examples_labels_the_text_and_respects_the_budget(tmp_path: Path) -> None:
    root = project_with(tmp_path, "a.py")
    memory = Memory(root)
    memory.record(make_receipt("t-1", "alpha fix", diff_for("a.py", "a = 1")))
    memory.record(make_receipt("t-2", "alpha repair", diff_for("a.py", "a = 2 # " + "q" * 3000)))
    lessons = memory.similar("alpha")
    text = render_examples(lessons, 1200)
    assert text.startswith("Earlier fixes in this project, for style only")
    assert "Task: alpha fix" in text
    assert render_examples([], 1200) == "" and render_examples(lessons, 0) == ""
    from qedcode.project.tokens import estimate_tokens

    assert estimate_tokens(render_examples(lessons, 300)) <= 320  # cut to fit
    assert render_examples(lessons, 30) == ""  # too small to be useful


# ------------------------------------------------------------------ the engine: apply learns, solve uses


def coder_prompts(model: FakeModel) -> list[str]:
    return [c.messages[-1]["content"] for c in model.calls if c.tag.startswith("coder")]


def solve_and_apply(repo: Path, config: GlobalConfig):
    model = FakeModel(script_for(ZOO))
    result = api.solve(
        repo, "paginate drops the last item", config=config, client=model, apply=True
    )
    return result, model


def test_apply_remembers_the_fix_and_the_next_task_sees_it(tmp_path: Path) -> None:
    repo = zoo_git_repo(tmp_path)
    config = GlobalConfig()
    first, first_model = solve_and_apply(repo, config)
    assert first.status == "done" and "will remember this fix" in first.message
    assert not any("Earlier fixes" in p for p in coder_prompts(first_model))  # nothing learned yet
    (lesson,) = api.memory_list(repo)
    assert "src/shop/pages.py" in lesson.files and lesson.grade == "A"

    second_model = FakeModel(script_for(ZOO))
    api.solve(
        repo,
        "paginate loses the final item on a page",
        speed=Speed.FAST,
        config=config,
        client=second_model,
    )
    prompt = coder_prompts(second_model)[0]
    assert "Earlier fixes in this project, for style only" in prompt
    assert "Task: paginate drops the last item" in prompt and "src/shop/pages.py" in prompt
    assert prompt.index("Earlier fixes") < prompt.index("CODE (current contents):")


def test_nothing_is_remembered_when_the_fix_is_not_applied_or_learning_is_off(
    tmp_path: Path,
) -> None:
    repo = zoo_git_repo(tmp_path)
    config = GlobalConfig()
    api.solve(
        repo, "paginate drops the last item", config=config, client=FakeModel(script_for(ZOO))
    )
    assert api.memory_list(repo) == []  # solved but never applied
    off = GlobalConfig()
    off.engine.learn = False
    result, _ = solve_and_apply(repo, off)
    assert result.status == "done" and "remember" not in result.message
    assert api.memory_list(repo) == []


def test_cloud_mode_and_small_contexts_get_no_examples(tmp_path: Path) -> None:
    repo = zoo_git_repo(tmp_path)
    solve_and_apply(repo, GlobalConfig())
    request = "paginate loses the final item on a page"
    cloud = FakeModel(script_for(ZOO))
    api.solve(repo, request, speed=Speed.FAST, mode=Mode.CLOUD, config=GlobalConfig(), client=cloud)
    assert not any("Earlier fixes" in p for p in coder_prompts(cloud))
    small = FakeModel(script_for(ZOO), context_tokens=4096)
    api.solve(repo, request, speed=Speed.FAST, config=GlobalConfig(), client=small)
    assert not any("Earlier fixes" in p for p in coder_prompts(small))


def test_the_helper_never_receives_the_examples(tmp_path: Path) -> None:
    repo = zoo_git_repo(tmp_path)
    solve_and_apply(repo, GlobalConfig())
    request = "paginate loses the final item on a page"
    writer = script_for(ZOO)["test_writer"] * 2  # the test passes on the fixed code: asked twice
    local = FakeModel(script_for(ZOO, coder=["PLAN:\n- nothing\n"] * 6, test_writer=writer))
    helper = FakeModel({"coder": script_for(ZOO)["coder"][:1]}, name="fake:helper")
    config = GlobalConfig()
    config.helper.ask_before_use = False  # pre-approved, as an MCP run would need
    api.solve(
        repo,
        request,
        speed=Speed.NORMAL,
        mode=Mode.MIX,
        config=config,
        client=local,
        helper_client=helper,
    )
    assert any("Earlier fixes" in p for p in coder_prompts(local))
    assert helper.calls and not any("Earlier fixes" in p for p in coder_prompts(helper))


def test_the_prompt_is_unchanged_when_there_are_no_examples(tmp_path: Path) -> None:
    repo = zoo_git_repo(tmp_path)
    model = FakeModel(script_for(ZOO))
    api.solve(repo, "paginate drops the last item", config=GlobalConfig(), client=model)
    prompt = coder_prompts(model)[0]
    assert "Earlier fixes" not in prompt and "{examples_block}" not in prompt
    assert "\nCODE (current contents):\n" in prompt


# ------------------------------------------------------------------ the command


def test_qed_memory_lists_forgets_and_clears(tmp_path: Path, monkeypatch) -> None:
    repo = zoo_git_repo(tmp_path)
    monkeypatch.chdir(repo)
    runner = CliRunner()
    empty = runner.invoke(app, ["memory"])
    assert empty.exit_code == 0 and "Nothing is remembered yet" in empty.output
    solve_and_apply(repo, GlobalConfig())
    listed = runner.invoke(app, ["memory", "list"])
    assert "Remembered fixes" in listed.output and "paginate drops" in listed.output
    assert "qed memory forget" in listed.output
    assert runner.invoke(app, ["memory"]).output == listed.output  # bare `qed memory` lists
    missing = runner.invoke(app, ["memory", "forget", "99"])
    assert missing.exit_code == 1 and "no remembered fix number 99" in missing.output
    (lesson,) = api.memory_list(repo)
    gone = runner.invoke(app, ["memory", "forget", str(lesson.id)])
    assert gone.exit_code == 0 and f"Forgot fix {lesson.id}." in gone.output
    solve_and_apply_again = api.memory_list(repo)
    assert solve_and_apply_again == []
    assert "Nothing is remembered." in runner.invoke(app, ["memory", "clear"]).output


def test_qed_memory_clear_asks_first(tmp_path: Path, monkeypatch) -> None:
    repo = project_with(tmp_path, "a.py")
    Memory(repo).record(make_receipt("t-1", "alpha fix", diff_for("a.py")))
    monkeypatch.chdir(repo)
    declined = CliRunner().invoke(app, ["memory", "clear"], input="n\n")
    assert declined.exit_code == 1 and len(api.memory_list(repo)) == 1
    done = CliRunner().invoke(app, ["memory", "clear"], input="y\n")
    assert "Forgot 1 fix(es)." in done.output and api.memory_list(repo) == []
