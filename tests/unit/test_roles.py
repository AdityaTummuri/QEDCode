from __future__ import annotations

from pathlib import Path

import pytest

from qedcode.engine.roles import (
    CODER_MAX_TOKENS_SURE,
    ROLE_SETTINGS,
    IntakeResult,
    Roles,
    fallback_explanation,
    format_prompt,
    load_prompt,
    parse_intake,
    parse_locator,
)
from qedcode.models.schemas import Candidate, Event, Speed, TaskKind, TaskSpec
from qedcode.project.context import (
    ContextFile,
    condense_file,
    files_block,
    fit_files,
    repro_test_path,
    task_keywords,
)
from qedcode.project.tokens import estimate_tokens, prompt_budget
from qedcode.runtime.fake import FakeModel
from qedcode.verify.cascade import Cancelled
from tests.conftest import write_files

FENCE = "```"
SPEC = TaskSpec(
    id="t-1", request="paginate drops the last item", acceptance=["each page has `size` items"]
)
GOOD_EDIT = (
    "PLAN:\n- fix the end index\n\n"
    "EDIT search_replace path=src/shop/pages.py\n<<<<<<< SEARCH\n    end = start + size - 1\n"
    "=======\n    end = start + size\n>>>>>>> REPLACE\n"
)
GOOD_TEST = (
    f"EDIT create_file path=tests/whatever.py\n{FENCE}python\n"
    "from shop.pages import paginate\n\n\ndef test_full_page():\n    assert paginate([1, 2, 3], 1, 2) == [1, 2]\n"
    f"{FENCE}\nFAILS NOW BECAUSE: the last item of each page is dropped\n"
)
FILES = [ContextFile("src/shop/pages.py", "def paginate(items, page, size):\n    return items\n")]


def roles_with(script: dict, **kw) -> tuple[Roles, FakeModel, list[Event]]:
    model = FakeModel(script, **{k: v for k, v in kw.items() if k in ("context_tokens",)})
    events: list[Event] = []
    roles = Roles(
        model,
        run_dir=kw.get("run_dir"),
        on_event=events.append,
        cancelled=kw.get("cancelled"),
        on_token=kw.get("on_token"),
    )
    return roles, model, events


# ------------------------------------------------------------------ prompt templates


def test_templates_follow_the_brief_word_for_word() -> None:
    assert load_prompt("system").startswith("You are the coding model inside QEDCode.")
    assert "KIND: bugfix | feature | refactor | test | docs | other" in load_prompt("intake")
    assert (
        "QUESTION: <one short question only if the task cannot be understood, otherwise NONE>"
        in load_prompt("intake")
    )
    assert "SEARCH HITS FOR WORDS FROM THE TASK:" in load_prompt("locator")
    assert "Write ONE new pytest file with exactly ONE small test" in load_prompt("test_writer")
    assert "FAILS NOW BECAUSE: <one line>" in load_prompt("test_writer")
    assert (
        "Make the smallest change that completes the task. Do not edit or create test files."
        in load_prompt("coder")
    )
    assert "YOUR LAST CHANGE:" in load_prompt("repairer") and "WHAT WENT WRONG:" in load_prompt(
        "repairer"
    )
    assert "Explain this code change to a beginner in 2 to 4 short sentences:" in load_prompt(
        "explainer"
    )
    cheat = load_prompt("protocol_cheatsheet")
    for op in (
        "replace_symbol",
        "insert_after_symbol",
        "add_import",
        "search_replace",
        "create_file",
    ):
        assert f"EDIT {op}" in cheat
    assert "Your reply did not follow the required format: {problem}." in load_prompt("reask")


def test_braces_in_user_content_never_break_formatting() -> None:
    text = format_prompt("intake", request="fix {name} and {0} and }{ in the f-string")
    assert "fix {name} and {0} and }{ in the f-string" in text
    spec = TaskSpec(id="t", request="handle {braces} {{x}}")
    roles, model, _ = roles_with({"coder": [GOOD_EDIT]})
    files = [ContextFile("a.py", "x = f'{y}'\nd = {'a': 1}\n")]
    roles.coder(spec, files, None, 0.2)
    prompt = model.calls[0].prompt
    assert "handle {braces} {{x}}" in prompt and "x = f'{y}'" in prompt and "d = {'a': 1}" in prompt


def test_role_settings_match_the_brief() -> None:
    assert ROLE_SETTINGS == {
        "intake": (0.1, 300),
        "locator": (0.1, 200),
        "test_writer": (0.3, 1200),
        "coder": (None, 2000),
        "repairer": (0.3, 2000),
        "explainer": (0.3, 250),
        "safety_net": (0.3, 1500),  # AGENTS.md 15.11
        "hunter": (0.5, 1500),  # AGENTS.md 15.12
    }
    assert CODER_MAX_TOKENS_SURE == 3000


# ------------------------------------------------------------------ intake


def test_intake_good_reply() -> None:
    reply = "KIND: bugfix\nACCEPTANCE:\n- paginate returns size items\n- last page keeps its last item\nQUESTION: NONE\n"
    roles, model, _ = roles_with({"intake": [reply]})
    result = roles.intake("paginate drops the last item", Speed.NORMAL)
    assert result.kind is TaskKind.BUGFIX and result.ok and result.question is None
    assert result.acceptance == ["paginate returns size items", "last page keeps its last item"]
    call = model.calls[0]
    assert (call.tag, call.temperature, call.max_tokens) == ("intake", 0.1, 300)
    assert call.messages[0]["role"] == "system" and call.messages[0]["content"].startswith(
        "You are the coding model"
    )
    assert "TASK:\npaginate drops the last item" in call.messages[1]["content"]


def test_intake_question_and_formatting_leniency() -> None:
    reply = "kind: Feature\nacceptance:\n* one\n1. two\n2) three\n- four\n- five\nQuestion: Which file should hold it?\n"
    result, problem = parse_intake(reply)
    assert problem == "" and result.kind is TaskKind.FEATURE
    assert result.acceptance == ["one", "two", "three", "four"]  # at most 4
    assert result.question == "Which file should hold it?"
    none_variants = [
        parse_intake(f"KIND: docs\nQUESTION: {v}")[0].question for v in ("none", "None.", "N/A")
    ]
    assert none_variants == [None, None, None]


@pytest.mark.parametrize(
    ("reply", "fragment"),
    [("hello", "KIND: line is missing"), ("KIND: banana", "KIND must be one of")],
)
def test_intake_parse_problems(reply: str, fragment: str) -> None:
    result, problem = parse_intake(reply)
    assert result is None and fragment in problem


def test_intake_bad_then_good_uses_one_format_reask() -> None:
    roles, model, events = roles_with(
        {"intake": ["I will think about it", "KIND: bugfix\nACCEPTANCE:\n- ok\nQUESTION: NONE"]}
    )
    result = roles.intake("fix it")
    assert result.kind is TaskKind.BUGFIX and result.ok
    assert len(model.calls) == 2
    second = model.calls[1].messages
    assert second[2] == {"role": "assistant", "content": "I will think about it"}
    assert second[3]["role"] == "user"
    assert second[3]["content"].startswith(
        "Your reply did not follow the required format: the KIND: line is missing."
    )
    assert second[3]["content"].endswith("Write nothing else.")
    assert any(e.kind == "warning" and "wrong format" in e.message for e in events)


def test_intake_bad_after_the_reask_degrades_gracefully() -> None:
    roles, model, events = roles_with({"intake": ["nonsense", "still nonsense"]})
    result = roles.intake("fix it")
    assert result == IntakeResult(kind=TaskKind.OTHER, acceptance=[], question=None, ok=False)
    assert len(model.calls) == 2  # never a third try
    assert any("Could not understand the intake reply" in e.message for e in events)


# ------------------------------------------------------------------ locator


def test_locator_good_and_lenient_replies() -> None:
    roles, model, _ = roles_with(
        {"locator": ["FILE: src/shop/pages.py\nFILE: tests/test_pages.py\n"]}
    )
    files = roles.locator(
        SPEC, "src/shop/pages.py: paginate, page_count", "pages.py:5: def paginate", 5
    )
    assert files == ["src/shop/pages.py", "tests/test_pages.py"]
    call = model.calls[0]
    assert (call.tag, call.temperature, call.max_tokens) == ("locator", 0.1, 200)
    assert (
        "src/shop/pages.py: paginate, page_count" in call.prompt
        and "pages.py:5: def paginate" in call.prompt
    )
    assert "(at most 5)" in call.prompt and "- each page has `size` items" in call.prompt
    messy = (
        'Sure:\n- `src/a.py`\n* FILE: "src/b.py"\nsrc\\c.py\nFILE: src/a.py\nnot a path at all\n'
    )
    paths, problem = parse_locator(messy, 5)
    assert problem == "" and paths == ["src/a.py", "src/b.py", "src/c.py"]
    assert parse_locator("FILE: a.py\nFILE: b.py\nFILE: c.py", 2)[0] == ["a.py", "b.py"]


def test_locator_failure_returns_an_empty_list_after_one_reask() -> None:
    roles, model, _ = roles_with(
        {"locator": ["I would look at the pages module", "maybe the util module?"]}
    )
    assert roles.locator(SPEC, "", "", 3) == []
    assert len(model.calls) == 2
    assert "(no search hits)" in model.calls[0].prompt  # empty hits get a placeholder


# ------------------------------------------------------------------ test writer


def test_test_writer_forces_the_path_chosen_by_qedcode() -> None:
    roles, model, _ = roles_with({"test_writer": [GOOD_TEST]})
    example = ContextFile("tests/test_pages.py", "\n".join(f"line {i}" for i in range(100)))
    result = roles.test_writer(SPEC, FILES, example, "tests/test_qed_paginate.py", label="a1")
    assert result.path == "tests/test_qed_paginate.py"  # not the path the model wrote
    assert (
        "def test_full_page" in result.content
        and result.reason == "the last item of each page is dropped"
    )
    call = model.calls[0]
    assert (call.tag, call.temperature, call.max_tokens) == ("test_writer", 0.3, 1200)
    assert "EDIT create_file path=tests/test_qed_paginate.py" in call.prompt
    assert "HOW EXISTING TESTS LOOK (tests/test_pages.py):" in call.prompt
    assert "line 39" in call.prompt and "line 40" not in call.prompt  # 40 lines of the example
    assert "=== src/shop/pages.py ===" in call.prompt


def test_test_writer_without_existing_tests() -> None:
    roles, model, _ = roles_with({"test_writer": [GOOD_TEST]})
    assert roles.test_writer(SPEC, FILES, None, "tests/test_qed_x.py") is not None
    assert "no existing tests to copy from" in model.calls[0].prompt


def test_test_writer_reasks_when_there_is_no_test_function() -> None:
    no_test = f"EDIT create_file path=t.py\n{FENCE}python\nX = 1\n{FENCE}\n"
    roles, model, _ = roles_with({"test_writer": [no_test, GOOD_TEST]})
    result = roles.test_writer(SPEC, FILES, None, "tests/test_qed_x.py")
    assert result is not None and len(model.calls) == 2
    assert "at least one function named test_" in model.calls[1].messages[-1]["content"]


def test_test_writer_gives_up_after_the_reask() -> None:
    roles, model, _ = roles_with({"test_writer": ["no edits here", "none here either"]})
    assert roles.test_writer(SPEC, FILES, None, "tests/test_qed_x.py") is None
    assert len(model.calls) == 2


# ------------------------------------------------------------------ coder


def test_coder_good_reply_and_prompt_shape() -> None:
    roles, model, _ = roles_with({"coder": [GOOD_EDIT]})
    result = roles.coder(SPEC, FILES, "def test_x():\n    assert 1\n", 0.7, label="c2")
    assert result.ok and len(result.reply.edits) == 1 and result.reply.plan == ["fix the end index"]
    call = model.calls[0]
    assert (call.tag, call.temperature, call.max_tokens) == ("coder", 0.7, 2000)
    prompt = call.messages[1]["content"]
    assert prompt.startswith(
        "TASK: paginate drops the last item\nACCEPTANCE:\n- each page has `size` items\n"
    )
    assert "This test must pass after your change:\ndef test_x():" in prompt
    assert (
        "CODE (current contents):\n=== src/shop/pages.py ===\ndef paginate(items, page, size):\n    return items"
        in prompt
    )
    assert (
        "Make the smallest change that completes the task. Do not edit or create test files."
        in prompt
    )
    assert prompt.rstrip().endswith("...the whole file...\n```")  # the cheat sheet comes last
    assert "1 |" not in prompt  # no line numbers


def test_coder_without_a_repro_has_no_repro_block_and_sure_gets_more_tokens() -> None:
    roles, model, _ = roles_with({"coder": [GOOD_EDIT, GOOD_EDIT]})
    roles.coder(SPEC, FILES, None, 0.2)
    assert "This test must pass" not in model.calls[0].prompt
    sure = SPEC.model_copy(update={"speed": Speed.SURE})
    roles.coder(sure, FILES, None, 0.2)
    assert model.calls[1].max_tokens == 3000


def test_coder_reasks_on_a_reply_without_edits() -> None:
    roles, model, _ = roles_with(
        {"coder": ["The bug is in paginate, change the end index.", GOOD_EDIT]}
    )
    result = roles.coder(SPEC, FILES, None, 0.2)
    assert result.ok and len(model.calls) == 2
    assert "no EDIT blocks found" in model.calls[1].messages[-1]["content"]


def test_coder_still_bad_after_the_reask_returns_the_unusable_reply() -> None:
    roles, model, _ = roles_with(
        {"coder": ["no idea", "EDIT replace_symbol path=a.py\n```\ncode\n```\n"]}
    )
    result = roles.coder(SPEC, FILES, None, 0.2)
    assert not result.ok and result.reply.edits == []
    assert any("missing symbol=NAME" in e for e in result.reply.errors)
    assert len(model.calls) == 2


def test_coder_reply_with_a_partial_edit_is_re_asked() -> None:
    partial = (
        GOOD_EDIT + f"EDIT replace_symbol path=a.py\n{FENCE}\ndef f(): pass\n{FENCE}\n"
    )  # second edit lacks symbol=
    roles, model, _ = roles_with({"coder": [partial, GOOD_EDIT]})
    assert roles.coder(SPEC, FILES, None, 0.2).ok
    assert "EDIT 2" in model.calls[1].messages[-1]["content"]


# ------------------------------------------------------------------ repairer


def test_repairer_prompt_has_the_diff_the_failure_and_the_current_code() -> None:
    roles, model, _ = roles_with({"repairer": [GOOD_EDIT]})
    near = Candidate(id="c1", role="coder", diff="--- a/x\n+++ b/x\n@@\n-old\n+new\n")
    current = [
        ContextFile("src/shop/pages.py", "def paginate(items, page, size):\n    return new\n")
    ]
    result = roles.repairer(
        SPEC, near, "FAILED tests.test_pages::test_x\nassert 1 == 2", current, label="r1"
    )
    assert result.ok
    call = model.calls[0]
    assert (call.tag, call.temperature, call.max_tokens) == ("repairer", 0.3, 2000)
    prompt = call.messages[1]["content"]
    assert "YOUR LAST CHANGE:\n```diff\n--- a/x\n+++ b/x\n@@\n-old\n+new\n```" in prompt
    assert "WHAT WENT WRONG:\nFAILED tests.test_pages::test_x\nassert 1 == 2" in prompt
    assert (
        "CURRENT CODE OF THE CHANGED FILES (your change is already applied):\n=== src/shop/pages.py ==="
        in prompt
    )
    assert "Edit the CURRENT code above." in prompt


# ------------------------------------------------------------------ explainer


def test_explainer_returns_the_model_text_without_a_reask() -> None:
    roles, model, _ = roles_with(
        {"explainer": ["  The end index was one short.  Now it is right.  "]}
    )
    text = roles.explainer(SPEC, "-a\n+b", "Grade A", ["src/shop/pages.py"])
    assert text == "The end index was one short.  Now it is right."
    call = model.calls[0]
    assert (call.tag, call.temperature, call.max_tokens) == ("explainer", 0.3, 250)
    assert "CHECKS: Grade A" in call.prompt and "```diff\n-a\n+b\n```" in call.prompt


def test_explainer_falls_back_to_a_template_sentence() -> None:
    roles, model, _ = roles_with({"explainer": ["   "]})
    text = roles.explainer(SPEC, "d", "Grade B", ["a.py", "b.py"])
    assert text == fallback_explanation(["a.py", "b.py"], "Grade B")
    assert "a.py, b.py" in text and len(model.calls) == 1  # none: no re-ask for the explainer
    broken, _, _ = roles_with({})  # no script at all: the FakeModel raises
    assert "QEDCode changed your project" in broken.explainer(SPEC, "d", "Grade C", [])


# ------------------------------------------------------------------ saved prompts, cancel


def test_every_prompt_and_reply_is_saved_in_order(tmp_path: Path) -> None:
    run_dir = tmp_path / ".qed" / "runs" / "t-1"
    script = {
        "intake": ["bad", "KIND: docs\nACCEPTANCE:\n- x\nQUESTION: NONE"],
        "coder": [GOOD_EDIT],
    }
    roles, _, _ = roles_with(script, run_dir=run_dir)
    roles.intake("write docs")
    roles.coder(SPEC, FILES, None, 0.2, label="c1")
    names = sorted(p.name for p in run_dir.iterdir())
    assert names == [
        "01-intake-prompt.txt",
        "02-intake-reply.txt",
        "03-intake-reask-prompt.txt",
        "04-intake-reask-reply.txt",
        "05-coder-c1-prompt.txt",
        "06-coder-c1-reply.txt",
    ]
    prompt = (run_dir / "05-coder-c1-prompt.txt").read_text(encoding="utf-8")
    assert (
        prompt.startswith("[system]\nYou are the coding model")
        and "[user]\nTASK: paginate" in prompt
    )
    assert (run_dir / "06-coder-c1-reply.txt").read_text(encoding="utf-8") == GOOD_EDIT
    assert "Your reply did not follow" in (run_dir / "03-intake-reask-prompt.txt").read_text(
        encoding="utf-8"
    )


def test_cancel_before_a_call_and_during_streaming() -> None:
    roles, model, _ = roles_with({"coder": [GOOD_EDIT]}, cancelled=lambda: True)
    with pytest.raises(Cancelled):
        roles.coder(SPEC, FILES, None, 0.2)
    assert model.calls == []  # nothing was sent
    state = {"n": 0}

    def cancel_on_second_check() -> bool:
        state["n"] += 1
        return state["n"] >= 2

    roles2, model2, _ = roles_with({"coder": [GOOD_EDIT]}, cancelled=cancel_on_second_check)
    with pytest.raises(Cancelled):
        roles2.coder(SPEC, FILES, None, 0.2)
    assert len(model2.calls) == 1  # it started, then the token callback aborted it


def test_tokens_are_forwarded_with_the_role_tag() -> None:
    seen: list[tuple[str, str]] = []
    roles, _, _ = roles_with(
        {"coder": [GOOD_EDIT]}, on_token=lambda tag, piece: seen.append((tag, piece))
    )
    roles.coder(SPEC, FILES, None, 0.2)
    assert seen and seen[0][0] == "coder" and "EDIT search_replace" in seen[0][1]


# ------------------------------------------------------------------ context budget


BIG = "".join(f'def func_{i}(x):\n    """Doc {i}."""\n    return x + {i}\n\n\n' for i in range(200))


def test_estimate_tokens_and_budget() -> None:
    assert (
        estimate_tokens("") == 0
        and estimate_tokens("a" * 35) == 10
        and estimate_tokens("a" * 36) == 11
    )
    assert prompt_budget(16384, 2000) == 16384 - 2000 - 512
    assert prompt_budget(1000, 2000) == 256  # never below a sane minimum


def test_files_block_format() -> None:
    block = files_block([ContextFile("a.py", "A = 1\n"), ContextFile("pkg/b.py", "B = 2")])
    assert block == "=== a.py ===\nA = 1\n\n=== pkg/b.py ===\nB = 2"


def test_over_budget_drops_files_from_the_end_first() -> None:
    files = [ContextFile(f"f{i}.py", "x = 1\n" * 400) for i in range(4)]
    kept = fit_files(files, estimate_tokens(files_block(files[:2])) + 5)
    assert [f.path for f in kept] == ["f0.py", "f1.py"] and not any(f.condensed for f in kept)
    assert fit_files(files, 10**6) == files


def test_a_single_huge_file_is_condensed_to_relevant_symbols_plus_an_outline() -> None:
    kept = fit_files([ContextFile("big.py", BIG)], 600, {"func_7"})
    (only,) = kept
    assert only.condensed and estimate_tokens(files_block(kept)) <= 600
    assert 'def func_7(x):\n    """Doc 7."""\n    return x + 7' in only.content  # relevant: in full
    assert (
        "def func_8(x):" in only.content and "return x + 8" not in only.content
    )  # others: signature only
    assert only.content.startswith("# (outline view")


def test_condense_class_and_module_header() -> None:
    src = (
        '"""Module doc."""\nimport os\n\nLIMIT = 10\n\n\nclass Store:\n    """A store."""\n\n'
        "    def rotate(self, token):\n        return token * 2\n\n    def issue(self):\n        return 'x'\n\n\n"
        "def helper():\n    return 1\n"
    )
    out = condense_file(src, {"rotate"})  # `rotate` is a method name, not a top-level symbol
    assert "import os" in out and "LIMIT = 10" in out and "Module doc" in out
    assert "class Store:" in out and "def rotate(self, token):" in out and "def issue(self):" in out
    assert "return token * 2" not in out and "return 'x'" not in out and "return 1" not in out
    whole = condense_file(src, {"store"})
    assert "return token * 2" in whole  # a relevant class is kept whole
    assert condense_file("def broken(:\n", {"x"}) == "def broken(:\n"


def test_the_coder_prompt_respects_a_small_context_window() -> None:
    roles, model, _ = roles_with({"coder": [GOOD_EDIT]}, context_tokens=2600)
    files = [ContextFile(f"src/m{i}.py", BIG) for i in range(3)]
    roles.coder(SPEC, files, None, 0.2)
    prompt = model.calls[0].messages[1]["content"]
    assert (
        estimate_tokens(prompt) <= prompt_budget(2600, 2000) + 600
    )  # fitted (fixed text is extra)
    assert "=== src/m0.py ===" in prompt and "=== src/m2.py ===" not in prompt


def test_task_keywords_and_repro_test_path(tmp_path: Path) -> None:
    assert {"paginate", "last", "item"} <= task_keywords("paginate drops the last item (x)")
    assert (
        repro_test_path("Fix: paginate drops the last item!", ["tests/test_a.py"], tmp_path)
        == "tests/test_qed_fix_paginate_drops_the_last.py"
    )
    assert repro_test_path("x", [], tmp_path) == "tests/test_qed_x.py"
    assert (
        repro_test_path(
            "x", ["pkg/tests/test_a.py", "pkg/tests/test_b.py", "t/test_c.py"], tmp_path
        )
        == "pkg/tests/test_qed_x.py"
    )
    assert repro_test_path("x", ["test_root.py"], tmp_path) == "test_qed_x.py"
    write_files(tmp_path, {"tests/test_qed_x.py": ""})
    assert repro_test_path("x", [], tmp_path) == "tests/test_qed_x_2.py"
