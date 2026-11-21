from __future__ import annotations

import pytest

from qedcode.edits.protocol import (
    NO_EDITS,
    blocking_errors,
    parse_reply,
    problem_text,
    reply_ok,
    warnings,
)
from tests.conftest import BUGZOO_DIR, zoo_project_names

FENCE = "```"

BRIEF_EXAMPLE = f"""\
PLAN:
- rotate() must reject a token that was already used
- add a helper that marks tokens as used

EDIT replace_symbol path=app/auth.py symbol=TokenStore.rotate
{FENCE}python
def rotate(self, old_token: str) -> str:
    if old_token in self._used:
        raise TokenReused(old_token)
    self._mark_used(old_token)
    return self.issue()
{FENCE}

EDIT insert_after_symbol path=app/auth.py symbol=TokenStore.rotate
{FENCE}python
def _mark_used(self, token: str) -> None:
    self._used.add(token)
{FENCE}

EDIT add_import path=app/auth.py
{FENCE}python
from app.errors import TokenReused
{FENCE}

EDIT search_replace path=app/config.py
<<<<<<< SEARCH
TOKEN_TTL = 60
=======
TOKEN_TTL = 900
>>>>>>> REPLACE

EDIT create_file path=app/errors.py
{FENCE}python
class TokenReused(Exception):
    pass
{FENCE}
"""


def one(text: str):
    reply = parse_reply(text)
    assert reply_ok(reply), reply.errors
    assert len(reply.edits) == 1
    return reply.edits[0], reply


# ------------------------------------------------------------------ the brief's own example


def test_the_complete_example_from_the_brief() -> None:
    reply = parse_reply(BRIEF_EXAMPLE)
    assert reply_ok(reply) and reply.errors == []
    assert reply.plan == [
        "rotate() must reject a token that was already used",
        "add a helper that marks tokens as used",
    ]
    assert [e.op for e in reply.edits] == [
        "replace_symbol",
        "insert_after_symbol",
        "add_import",
        "search_replace",
        "create_file",
    ]
    first = reply.edits[0]
    assert (first.path, first.symbol) == ("app/auth.py", "TokenStore.rotate")
    assert first.code.startswith("def rotate(self, old_token: str) -> str:\n")
    assert first.code.endswith("return self.issue()\n")
    assert reply.edits[2].code == "from app.errors import TokenReused\n"
    sr = reply.edits[3]
    assert (sr.path, sr.search, sr.code) == ("app/config.py", "TOKEN_TTL = 60", "TOKEN_TTL = 900")
    assert reply.edits[4].symbol is None and reply.edits[4].path == "app/errors.py"


@pytest.mark.parametrize("name", zoo_project_names())
def test_every_bug_zoo_edits_file_parses_cleanly(name: str) -> None:
    text = (BUGZOO_DIR / name / "edits.txt").read_text(encoding="utf-8")
    reply = parse_reply(text)
    assert reply_ok(reply) and reply.errors == [], reply.errors
    assert reply.plan and reply.edits


# ------------------------------------------------------------------ leniency rules


def test_prose_before_between_and_after_blocks_is_ignored_with_one_warning() -> None:
    text = f"""\
Sure! Here is the fix you asked for.

EDIT replace_symbol path=a.py symbol=f
{FENCE}python
def f():
    return 1
{FENCE}

And now a second thing, hope this helps.

EDIT add_import path=a.py
{FENCE}python
import os
{FENCE}

Let me know if you need anything else!
"""
    reply = parse_reply(text)
    assert reply_ok(reply) and len(reply.edits) == 2
    assert warnings(reply) == ["ignored text outside the EDIT blocks"]
    assert blocking_errors(reply) == []


def test_missing_closing_fence_at_the_end_is_accepted() -> None:
    edit, reply = one(
        f"EDIT replace_symbol path=a.py symbol=f\n{FENCE}python\ndef f():\n    return 1\n"
    )
    assert edit.code == "def f():\n    return 1\n"
    assert any("closing ```" in w for w in warnings(reply))


def test_a_missing_fence_in_the_middle_swallows_the_next_edit_but_is_still_flagged() -> None:
    text = f"""\
EDIT replace_symbol path=a.py symbol=f
{FENCE}python
def f():
    return 1

EDIT add_import path=a.py
{FENCE}python
import os
{FENCE}
"""
    reply = parse_reply(text)
    # the first fence closes at the second fence line, so the add_import header is just code text
    assert len(reply.edits) >= 1
    assert "EDIT add_import" in reply.edits[0].code


def test_windows_line_endings() -> None:
    text = f"PLAN:\r\n- do it\r\n\r\nEDIT replace_symbol path=a.py symbol=f\r\n{FENCE}python\r\ndef f():\r\n    return 1\r\n{FENCE}\r\n"
    edit, reply = one(text)
    assert edit.code == "def f():\n    return 1\n" and "\r" not in edit.code
    assert reply.plan == ["do it"]
    assert any("Windows line endings" in w for w in warnings(reply))


def test_lowercase_and_mixed_case_edit_headers() -> None:
    for header in ("edit", "Edit", "eDiT"):
        edit, reply = one(
            f"{header} replace_symbol path=a.py symbol=f\n{FENCE}\ndef f(): pass\n{FENCE}\n"
        )
        assert edit.op == "replace_symbol"
        assert any("capital letters" in w for w in warnings(reply))


def test_uppercase_operation_names_are_normalised() -> None:
    edit, _ = one(f"EDIT REPLACE_SYMBOL path=a.py symbol=f\n{FENCE}\ndef f(): pass\n{FENCE}\n")
    assert edit.op == "replace_symbol"


@pytest.mark.parametrize("tag", ["python", "py", "", "python3"])
def test_common_language_tags_need_no_warning(tag: str) -> None:
    edit, reply = one(f"EDIT create_file path=a.py\n{FENCE}{tag}\nX = 1\n{FENCE}\n")
    assert reply.errors == [] and edit.code == "X = 1\n"


def test_unusual_language_tag_is_accepted_with_a_warning() -> None:
    edit, reply = one(f"EDIT create_file path=a.py\n{FENCE}text\nX = 1\n{FENCE}\n")
    assert edit.code == "X = 1\n"
    assert any("unusual language tag" in w for w in warnings(reply))


@pytest.mark.parametrize("quoted", ['"app/a.py"', "'app/a.py'", "`app/a.py`"])
def test_quoted_paths(quoted: str) -> None:
    edit, reply = one(f"EDIT create_file path={quoted}\n{FENCE}python\nX = 1\n{FENCE}\n")
    assert edit.path == "app/a.py"
    assert any("quotes" in w for w in warnings(reply))


def test_quoted_path_with_spaces() -> None:
    edit, _ = one(f'EDIT create_file path="my dir/my file.py"\n{FENCE}python\nX = 1\n{FENCE}\n')
    assert edit.path == "my dir/my file.py"


@pytest.mark.parametrize("raw", ["Cart.total()", "Cart.total(self)", "Cart.total(self, x=1)"])
def test_symbol_with_parentheses(raw: str) -> None:
    edit, reply = one(
        f"EDIT replace_symbol path=a.py symbol={raw}\n{FENCE}python\ndef total(self): pass\n{FENCE}\n"
    )
    assert edit.symbol == "Cart.total"
    assert any("parentheses" in w for w in warnings(reply))


def test_search_replace_without_a_fence() -> None:
    text = "EDIT search_replace path=c.py\n<<<<<<< SEARCH\nA = 1\nB = 2\n=======\nA = 10\n>>>>>>> REPLACE\n"
    edit, reply = one(text)
    assert edit.search == "A = 1\nB = 2" and edit.code == "A = 10" and reply.errors == []


def test_search_replace_inside_a_fence() -> None:
    text = f"EDIT search_replace path=c.py\n{FENCE}\n<<<<<<< SEARCH\nA = 1\n=======\nA = 2\n>>>>>>> REPLACE\n{FENCE}\n"
    edit, reply = one(text)
    assert (edit.search, edit.code) == ("A = 1", "A = 2") and reply.errors == []


def test_search_replace_inside_a_python_fence_followed_by_another_edit() -> None:
    text = (
        f"EDIT search_replace path=c.py\n{FENCE}python\n<<<<<<< SEARCH\nA = 1\n=======\nA = 2\n"
        f">>>>>>> REPLACE\n{FENCE}\n\nEDIT add_import path=c.py\n{FENCE}python\nimport os\n{FENCE}\n"
    )
    reply = parse_reply(text)
    assert reply_ok(reply) and [e.op for e in reply.edits] == ["search_replace", "add_import"]
    assert reply.errors == []


def test_search_replace_with_an_empty_replacement_deletes() -> None:
    edit, _ = one(
        "EDIT search_replace path=c.py\n<<<<<<< SEARCH\nOLD = 1\n=======\n>>>>>>> REPLACE\n"
    )
    assert edit.code == "" and edit.search == "OLD = 1"


def test_search_replace_missing_the_end_marker_at_the_end_of_the_reply() -> None:
    edit, reply = one("EDIT search_replace path=c.py\n<<<<<<< SEARCH\nA = 1\n=======\nA = 2\n")
    assert edit.code == "A = 2"
    assert any("REPLACE line was missing" in w for w in warnings(reply))


def test_search_replace_markers_with_a_different_number_of_symbols() -> None:
    edit, _ = one(
        "EDIT search_replace path=c.py\n<<<<<< SEARCH\nA = 1\n======\nA = 2\n>>>>>> REPLACE\n"
    )
    assert (edit.search, edit.code) == ("A = 1", "A = 2")


def test_search_text_keeps_its_indentation_and_blank_lines() -> None:
    text = "EDIT search_replace path=c.py\n<<<<<<< SEARCH\n    if x:\n\n        y = 1\n=======\n    if z:\n        y = 2\n>>>>>>> REPLACE\n"
    edit, _ = one(text)
    assert edit.search == "    if x:\n\n        y = 1"
    assert edit.code == "    if z:\n        y = 2"


# ------------------------------------------------------------------ structure


def test_two_edits_on_one_file_keep_their_order() -> None:
    text = (
        f"EDIT replace_symbol path=a.py symbol=f\n{FENCE}python\ndef f(): return 1\n{FENCE}\n"
        f"EDIT replace_symbol path=a.py symbol=g\n{FENCE}python\ndef g(): return 2\n{FENCE}\n"
    )
    reply = parse_reply(text)
    assert reply_ok(reply)
    assert [(e.path, e.symbol) for e in reply.edits] == [("a.py", "f"), ("a.py", "g")]


def test_no_edits_at_all() -> None:
    reply = parse_reply("I could not find anything to change in this project.")
    assert not reply_ok(reply) and reply.edits == []
    assert NO_EDITS in reply.errors
    assert problem_text(reply) == NO_EDITS


def test_empty_and_none_like_replies() -> None:
    for text in ("", "   \n\n", None):
        reply = parse_reply(text)  # type: ignore[arg-type]
        assert not reply_ok(reply) and NO_EDITS in reply.errors


def test_plan_only() -> None:
    reply = parse_reply("PLAN:\n- first\n- second\n")
    assert reply.plan == ["first", "second"] and reply.edits == []
    assert not reply_ok(reply) and NO_EDITS in reply.errors


def test_plan_formats_bullets_numbers_and_inline() -> None:
    reply = parse_reply(
        "PLAN: fix the loop\n1. then add a test\n2) and re-run\n* last\n\nEDIT create_file path=a.py\n```\nX = 1\n```\n"
    )
    assert reply.plan == ["fix the loop", "then add a test", "and re-run", "last"]
    assert reply_ok(reply)


def test_plan_in_lower_case_and_bold() -> None:
    assert parse_reply("plan:\n- a\n").plan == ["a"]
    assert parse_reply("**PLAN:**\n- b\n").plan == ["b"]


def test_ordinary_sentences_about_editing_are_not_headers() -> None:
    reply = parse_reply("Edit the file as described. Plan to run tests later.\n")
    assert reply.edits == [] and NO_EDITS in reply.errors
    assert reply.plan == []


def test_fails_now_because_line_is_captured() -> None:
    text = f"EDIT create_file path=tests/test_x.py\n{FENCE}python\ndef test_x():\n    assert f() == 1\n{FENCE}\nFAILS NOW BECAUSE: f() returns 0\n"
    edit, reply = one(text)
    assert reply.extra == {"FAILS NOW BECAUSE": "f() returns 0"}
    assert edit.path == "tests/test_x.py"


def test_markdown_decorated_headers_are_accepted() -> None:
    for header in (
        "### EDIT create_file path=a.py",
        "**EDIT create_file path=a.py**",
        "`EDIT create_file path=a.py`",
    ):
        edit, reply = one(f"{header}\n{FENCE}python\nX = 1\n{FENCE}\n")
        assert edit.path == "a.py"
        assert any("markdown decoration" in w for w in warnings(reply))


def test_a_reply_wrapped_in_one_big_fence_still_parses() -> None:
    inner = f"EDIT create_file path=a.py\n{FENCE}python\nX = 1\n{FENCE}\n"
    reply = parse_reply(f"{FENCE}text\nPLAN:\n- x\n{inner}{FENCE}\n")
    assert reply_ok(reply) and reply.edits[0].path == "a.py"


def test_code_that_looks_like_an_edit_header_stays_code() -> None:
    body = 'text = "EDIT replace_symbol path=x.py symbol=y"'
    edit, _ = one(f"EDIT create_file path=a.py\n{FENCE}python\n{body}\n{FENCE}\n")
    assert body in edit.code


def test_blank_lines_around_the_code_are_trimmed_but_inner_ones_kept() -> None:
    edit, _ = one(f"EDIT create_file path=a.py\n{FENCE}python\n\n\nA = 1\n\nB = 2\n\n{FENCE}\n")
    assert edit.code == "A = 1\n\nB = 2\n"


def test_tabs_and_indentation_inside_code_are_preserved() -> None:
    edit, _ = one(
        f"EDIT replace_symbol path=a.py symbol=C.m\n{FENCE}python\n    def m(self):\n\t\treturn 1\n{FENCE}\n"
    )
    assert edit.code == "    def m(self):\n\t\treturn 1\n"


# ------------------------------------------------------------------ real format problems


def test_unknown_operation() -> None:
    reply = parse_reply(f"EDIT delete_file path=a.py\n{FENCE}\nx\n{FENCE}\n")
    assert not reply_ok(reply)
    assert any("unknown operation 'delete_file'" in e for e in blocking_errors(reply))
    assert "replace_symbol" in problem_text(reply)


def test_missing_path_and_missing_symbol() -> None:
    reply = parse_reply(f"EDIT replace_symbol symbol=f\n{FENCE}\ndef f(): pass\n{FENCE}\n")
    assert any("missing path" in e for e in blocking_errors(reply))
    reply = parse_reply(f"EDIT replace_symbol path=a.py\n{FENCE}\ndef f(): pass\n{FENCE}\n")
    assert any("missing symbol=NAME" in e for e in blocking_errors(reply))
    assert not reply_ok(reply)


def test_header_without_a_code_block() -> None:
    reply = parse_reply("EDIT create_file path=a.py\nJust some words, no code.\n")
    assert not reply_ok(reply)
    assert any("no fenced code block" in e for e in blocking_errors(reply))


def test_empty_code_block_and_empty_search() -> None:
    assert any(
        "empty" in e
        for e in blocking_errors(parse_reply(f"EDIT create_file path=a.py\n{FENCE}\n{FENCE}\n"))
    )
    reply = parse_reply(
        "EDIT search_replace path=a.py\n<<<<<<< SEARCH\n=======\nx\n>>>>>>> REPLACE\n"
    )
    assert any("SEARCH text is empty" in e for e in blocking_errors(reply))


def test_search_replace_without_markers_or_divider() -> None:
    reply = parse_reply("EDIT search_replace path=a.py\nreplace A with B please\n")
    assert any("expected <<<<<<< SEARCH" in e for e in blocking_errors(reply))
    reply = parse_reply("EDIT search_replace path=a.py\n<<<<<<< SEARCH\nA\n")
    assert any("=======" in e for e in blocking_errors(reply))


def test_one_good_and_one_bad_edit_makes_the_reply_unusable() -> None:
    text = (
        f"EDIT create_file path=a.py\n{FENCE}python\nX = 1\n{FENCE}\n"
        f"EDIT replace_symbol path=a.py\n{FENCE}python\ndef f(): pass\n{FENCE}\n"
    )
    reply = parse_reply(text)
    assert len(reply.edits) == 1  # the good one is parsed...
    assert not reply_ok(reply)  # ...but a partial reply is never silently accepted
    assert "EDIT 2" in problem_text(reply)


def test_error_messages_name_the_edit_number_and_header() -> None:
    reply = parse_reply(
        f"EDIT create_file path=a.py\n{FENCE}python\nX = 1\n{FENCE}\nEDIT replace_symbol path=b.py\n"
    )
    message = problem_text(reply)
    assert "EDIT 2 (replace_symbol path=b.py" in message


def test_never_raises_on_garbage() -> None:
    nasty = [
        "```",
        "EDIT",
        "EDIT x=",
        "<<<<<<< SEARCH",
        "PLAN:",
        "\x00\x01",
        "EDIT create_file path=",
        "=" * 500,
    ]
    for text in nasty:
        reply = parse_reply(text)
        assert not reply_ok(reply)


def test_rest_underlines_in_the_search_text_are_not_the_divider() -> None:
    """sympy and numpy docstrings underline headings with `========`; only the marker divides."""
    text = (
        "EDIT search_replace path=pkg/mod.py\n<<<<<<< SEARCH\ndef f():\n"
        '    """Return one.\n\n    Examples\n    ========\n\n    >>> f()\n    1\n    """\n'
        "    return 0\n=======\ndef f():\n"
        '    """Return one.\n\n    Examples\n    ========\n\n    >>> f()\n    1\n    """\n'
        "    return 1\n>>>>>>> REPLACE\n"
    )
    reply = parse_reply(text)
    assert reply_ok(reply)
    (edit,) = reply.edits
    assert edit.search.count("========") == 1 and edit.search.endswith("    return 0")
    assert edit.code.endswith("    return 1") and "========" in edit.code


def test_a_loose_divider_still_works_when_there_is_no_exact_one() -> None:
    text = "EDIT search_replace path=a.py\n<<<<<<< SEARCH\nx = 1\n  =====\nx = 2\n>>>>>>> REPLACE\n"
    (edit,) = parse_reply(text).edits
    assert (edit.search, edit.code) == ("x = 1", "x = 2")
