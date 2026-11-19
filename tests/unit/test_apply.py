from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

from qedcode.edits.apply import apply_edits, apply_edits_report, list_symbols
from qedcode.edits.protocol import parse_reply, reply_ok
from qedcode.models.schemas import EditOp
from tests.conftest import write_files, zoo_project_names


def edit(op: str, path: str, code: str = "", symbol: str | None = None, search: str | None = None):
    return EditOp(op=op, path=path, symbol=symbol, search=search, code=code)  # type: ignore[arg-type]


def apply(tmp: Path, *edits: EditOp, role: str = "coder", **kw) -> list[str]:
    return apply_edits(tmp, list(edits), role, **kw)


def read(tmp: Path, rel: str) -> str:
    return (tmp / rel).read_bytes().decode("utf-8")


def check_parses(tmp: Path, rel: str) -> None:
    ast.parse(read(tmp, rel).replace("\r\n", "\n"))


MODULE = '''"""Module docstring."""

import os


def helper():
    return 1


@decorator
def decorated(x):
    return x


class Store:
    """A store."""

    def rotate(self, token):
        return token

    def issue(self):
        return "t"

    class Inner:
        def deep(self):
            return "deep"


async def fetch():
    return await thing()


def last():
    return "end"
'''


@pytest.fixture
def mod(tmp_path: Path) -> Path:
    write_files(tmp_path, {"app/mod.py": MODULE})
    return tmp_path


# ------------------------------------------------------------------ replace_symbol


def test_replace_a_top_level_function_leaves_everything_else_alone(mod: Path) -> None:
    errors = apply(
        mod, edit("replace_symbol", "app/mod.py", "def helper():\n    return 2\n", "helper")
    )
    assert errors == []
    expected = MODULE.replace("def helper():\n    return 1\n", "def helper():\n    return 2\n")
    assert read(mod, "app/mod.py") == expected


def test_replace_a_decorated_function_takes_its_decorator_with_it(mod: Path) -> None:
    code = "@decorator\n@other\ndef decorated(x):\n    return x * 2\n"
    assert apply(mod, edit("replace_symbol", "app/mod.py", code, "decorated")) == []
    text = read(mod, "app/mod.py")
    assert "@decorator\n@other\ndef decorated(x):\n    return x * 2\n" in text
    assert text.count("@decorator") == 1  # the old decorator line was part of the replaced span
    check_parses(mod, "app/mod.py")


def test_replacing_a_decorated_function_without_its_decorator_drops_it(mod: Path) -> None:
    assert (
        apply(
            mod,
            edit("replace_symbol", "app/mod.py", "def decorated(x):\n    return 0\n", "decorated"),
        )
        == []
    )
    assert "@decorator" not in read(mod, "app/mod.py")


def test_replace_a_method_re_indents_code_given_at_column_zero(mod: Path) -> None:
    code = (
        "def rotate(self, token):\n    if not token:\n        raise ValueError\n    return token\n"
    )
    assert apply(mod, edit("replace_symbol", "app/mod.py", code, "Store.rotate")) == []
    text = read(mod, "app/mod.py")
    assert (
        "    def rotate(self, token):\n        if not token:\n            raise ValueError\n        return token\n"
        in text
    )
    assert "    def issue(self):" in text
    check_parses(mod, "app/mod.py")


def test_indentation_rebasing_accepts_wrongly_indented_code(mod: Path) -> None:
    code = (
        "        def rotate(self, token):\n            return token.upper()\n"  # 8 spaces, needs 4
    )
    assert apply(mod, edit("replace_symbol", "app/mod.py", code, "Store.rotate")) == []
    assert "    def rotate(self, token):\n        return token.upper()\n" in read(mod, "app/mod.py")
    check_parses(mod, "app/mod.py")


def test_replace_a_nested_class_method(mod: Path) -> None:
    code = "def deep(self):\n    return 'deeper'\n"
    assert apply(mod, edit("replace_symbol", "app/mod.py", code, "Store.Inner.deep")) == []
    assert "        def deep(self):\n            return 'deeper'\n" in read(mod, "app/mod.py")
    check_parses(mod, "app/mod.py")


def test_replace_an_async_function(mod: Path) -> None:
    code = "async def fetch():\n    return None\n"
    assert apply(mod, edit("replace_symbol", "app/mod.py", code, "fetch")) == []
    assert "async def fetch():\n    return None\n" in read(mod, "app/mod.py")
    assert "await thing()" not in read(mod, "app/mod.py")


def test_replace_a_whole_class(mod: Path) -> None:
    code = 'class Store:\n    """New store."""\n\n    def only(self):\n        return 1\n'
    assert apply(mod, edit("replace_symbol", "app/mod.py", code, "Store")) == []
    text = read(mod, "app/mod.py")
    assert 'class Store:\n    """New store."""' in text
    assert "rotate" not in text and "Inner" not in text
    assert "async def fetch" in text and "def last" in text  # neighbours survive
    check_parses(mod, "app/mod.py")


def test_a_renamed_symbol_fails_and_suggests_insert_after_symbol(mod: Path) -> None:
    before = read(mod, "app/mod.py")
    errors = apply(
        mod, edit("replace_symbol", "app/mod.py", "def helper_renamed():\n    return 1\n", "helper")
    )
    assert len(errors) == 1
    assert errors[0].startswith("EDIT 1 (replace_symbol path=app/mod.py symbol=helper): ")
    assert "defines helper_renamed" in errors[0] and "'helper'" in errors[0]
    assert "insert_after_symbol" in errors[0]
    assert read(mod, "app/mod.py") == before


def test_symbol_not_found_lists_the_symbols_in_the_file(mod: Path) -> None:
    errors = apply(mod, edit("replace_symbol", "app/mod.py", "def rotat(): pass\n", "Store.rotat"))
    assert errors[0].startswith(
        "EDIT 1 (replace_symbol path=app/mod.py symbol=Store.rotat): symbol not found."
    )
    assert (
        "Symbols in this file: helper, decorated, Store, Store.rotate, Store.issue, Store.Inner, "
        "Store.Inner.deep, fetch, last."
    ) in errors[0]


def test_an_ambiguous_symbol_is_refused(tmp_path: Path) -> None:
    src = "class A:\n    @property\n    def x(self):\n        return 1\n\n    @x.setter\n    def x(self, v):\n        pass\n"
    write_files(tmp_path, {"a.py": src})
    errors = apply(tmp_path, edit("replace_symbol", "a.py", "def x(self): pass\n", "A.x"))
    assert "defined 2 times" in errors[0] and "search_replace" in errors[0]
    assert read(tmp_path, "a.py") == src


def test_new_code_that_does_not_parse(mod: Path) -> None:
    errors = apply(
        mod, edit("replace_symbol", "app/mod.py", "def helper(:\n    return 1\n", "helper")
    )
    assert "does not parse on its own" in errors[0]


def test_a_file_with_a_syntax_error_cannot_be_searched_for_symbols(tmp_path: Path) -> None:
    write_files(tmp_path, {"a.py": "def broken(:\n    pass\n"})
    errors = apply(tmp_path, edit("replace_symbol", "a.py", "def f(): pass\n", "f"))
    assert "syntax error on line 1" in errors[0] and "search_replace" in errors[0]


def test_extra_helper_definitions_next_to_the_symbol_are_accepted(mod: Path) -> None:
    code = "def _tidy(x):\n    return x\n\n\ndef helper():\n    return _tidy(5)\n"
    assert apply(mod, edit("replace_symbol", "app/mod.py", code, "helper")) == []
    text = read(mod, "app/mod.py")
    assert "def _tidy(x)" in text and "return _tidy(5)" in text
    check_parses(mod, "app/mod.py")


def test_multi_line_strings_inside_new_code_keep_their_exact_text(mod: Path) -> None:
    code = 'def rotate(self, token):\n    sql = """\nSELECT *\n  FROM t\n"""\n    return sql\n'
    assert apply(mod, edit("replace_symbol", "app/mod.py", code, "Store.rotate")) == []
    text = read(mod, "app/mod.py")
    assert (
        '        sql = """\nSELECT *\n  FROM t\n"""\n        return sql\n' in text
    )  # string body untouched
    check_parses(mod, "app/mod.py")


def test_tab_indented_files_keep_their_tabs(tmp_path: Path) -> None:
    src = "class C:\n\tdef a(self):\n\t\treturn 1\n\n\tdef b(self):\n\t\treturn 2\n"
    write_files(tmp_path, {"t.py": src})
    assert (
        apply(tmp_path, edit("replace_symbol", "t.py", "def a(self):\n\treturn 10\n", "C.a")) == []
    )
    assert (
        read(tmp_path, "t.py")
        == "class C:\n\tdef a(self):\n\t\treturn 10\n\n\tdef b(self):\n\t\treturn 2\n"
    )


# ------------------------------------------------------------------ insert_after_symbol


def test_insert_after_a_top_level_function_uses_two_blank_lines(mod: Path) -> None:
    code = "def added():\n    return 'new'\n"
    assert apply(mod, edit("insert_after_symbol", "app/mod.py", code, "helper")) == []
    text = read(mod, "app/mod.py")
    assert "def helper():\n    return 1\n\n\ndef added():\n    return 'new'\n\n\n@decorator" in text
    check_parses(mod, "app/mod.py")


def test_insert_after_a_method_uses_one_blank_line_and_the_method_indent(mod: Path) -> None:
    code = "def revoke(self, token):\n    return None\n"
    assert apply(mod, edit("insert_after_symbol", "app/mod.py", code, "Store.rotate")) == []
    text = read(mod, "app/mod.py")
    assert (
        "        return token\n\n    def revoke(self, token):\n        return None\n\n    def issue"
        in text
    )
    check_parses(mod, "app/mod.py")


def test_insert_after_the_last_definition_in_the_file(mod: Path) -> None:
    assert (
        apply(mod, edit("insert_after_symbol", "app/mod.py", "def tail():\n    pass\n", "last"))
        == []
    )
    text = read(mod, "app/mod.py")
    assert text.endswith('def last():\n    return "end"\n\n\ndef tail():\n    pass\n')
    check_parses(mod, "app/mod.py")


def test_insert_after_a_decorated_function_goes_after_its_body(mod: Path) -> None:
    assert (
        apply(
            mod,
            edit("insert_after_symbol", "app/mod.py", "def after_deco():\n    pass\n", "decorated"),
        )
        == []
    )
    text = read(mod, "app/mod.py")
    assert (
        "def decorated(x):\n    return x\n\n\ndef after_deco():\n    pass\n\n\nclass Store" in text
    )


def test_insert_after_a_nested_class_member(mod: Path) -> None:
    code = "def more(self):\n    return 1\n"
    assert apply(mod, edit("insert_after_symbol", "app/mod.py", code, "Store.Inner.deep")) == []
    assert '            return "deep"\n\n        def more(self):\n            return 1\n' in read(
        mod, "app/mod.py"
    )
    check_parses(mod, "app/mod.py")


def test_insert_after_a_missing_symbol(mod: Path) -> None:
    errors = apply(mod, edit("insert_after_symbol", "app/mod.py", "def x(): pass\n", "nope"))
    assert "symbol not found" in errors[0] and "Store.Inner.deep" in errors[0]


# ------------------------------------------------------------------ add_import


def test_add_import_goes_after_the_last_import(mod: Path) -> None:
    assert apply(mod, edit("add_import", "app/mod.py", "from datetime import timedelta\n")) == []
    text = read(mod, "app/mod.py")
    assert "import os\nfrom datetime import timedelta\n\n\ndef helper" in text


def test_add_import_skips_imports_that_are_already_there(mod: Path) -> None:
    before = read(mod, "app/mod.py")
    assert apply(mod, edit("add_import", "app/mod.py", "import os\n")) == []
    assert read(mod, "app/mod.py") == before
    code = "import os\nimport sys\n"
    assert apply(mod, edit("add_import", "app/mod.py", code)) == []
    text = read(mod, "app/mod.py")
    assert text.count("import os") == 1 and "import sys" in text


def test_add_import_with_a_module_docstring_and_no_imports(tmp_path: Path) -> None:
    write_files(tmp_path, {"a.py": '"""Doc."""\n\n\ndef f():\n    return 1\n'})
    assert apply(tmp_path, edit("add_import", "a.py", "import os\n")) == []
    assert read(tmp_path, "a.py") == '"""Doc."""\n\nimport os\n\n\ndef f():\n    return 1\n'


def test_add_import_with_no_docstring_and_no_imports(tmp_path: Path) -> None:
    write_files(tmp_path, {"a.py": "def f():\n    return 1\n"})
    assert apply(tmp_path, edit("add_import", "a.py", "import os\nimport re\n")) == []
    assert read(tmp_path, "a.py") == "import os\nimport re\n\ndef f():\n    return 1\n"
    check_parses(tmp_path, "a.py")


def test_add_import_after_a_shebang_and_docstring(tmp_path: Path) -> None:
    write_files(tmp_path, {"a.py": '#!/usr/bin/env python\n"""Doc."""\nX = 1\n'})
    assert apply(tmp_path, edit("add_import", "a.py", "import os\n")) == []
    text = read(tmp_path, "a.py")
    assert text.startswith('#!/usr/bin/env python\n"""Doc."""\n')
    assert text.index("import os") > text.index('"""Doc."""') and text.endswith("X = 1\n")


def test_add_import_after_future_imports(tmp_path: Path) -> None:
    write_files(tmp_path, {"a.py": "from __future__ import annotations\n\nX = 1\n"})
    assert apply(tmp_path, edit("add_import", "a.py", "import os\n")) == []
    assert read(tmp_path, "a.py").startswith("from __future__ import annotations\nimport os\n")


def test_add_import_keeps_a_multi_line_import_intact(mod: Path) -> None:
    code = "from typing import (\n    Any,\n    Dict,\n)\n"
    assert apply(mod, edit("add_import", "app/mod.py", code)) == []
    assert "from typing import (\n    Any,\n    Dict,\n)\n" in read(mod, "app/mod.py")
    check_parses(mod, "app/mod.py")


def test_add_import_refuses_other_code_and_non_python_files(mod: Path) -> None:
    errors = apply(mod, edit("add_import", "app/mod.py", "import os\nx = 1\n"))
    assert "accepts only `import" in errors[0]
    write_files(mod, {"notes.txt": "hi\n"})
    assert "only works on Python" in apply(mod, edit("add_import", "notes.txt", "import os\n"))[0]
    assert "do not parse" in apply(mod, edit("add_import", "app/mod.py", "import (\n"))[0]


# ------------------------------------------------------------------ search_replace


CONFIG = "# settings\nTOKEN_TTL = 60  # seconds\nRETRIES = 3\n\ndef f():\n    if RETRIES:\n        return TOKEN_TTL\n"


def test_search_replace_exact_once(tmp_path: Path) -> None:
    write_files(tmp_path, {"c.py": CONFIG})
    assert (
        apply(tmp_path, edit("search_replace", "c.py", "TOKEN_TTL = 900", search="TOKEN_TTL = 60"))
        == []
    )
    assert read(tmp_path, "c.py") == CONFIG.replace("TOKEN_TTL = 60", "TOKEN_TTL = 900")


def test_search_replace_multi_line_and_deletion(tmp_path: Path) -> None:
    write_files(tmp_path, {"c.py": CONFIG})
    search = "def f():\n    if RETRIES:\n        return TOKEN_TTL\n"
    assert apply(tmp_path, edit("search_replace", "c.py", "", search=search)) == []
    assert read(tmp_path, "c.py") == "# settings\nTOKEN_TTL = 60  # seconds\nRETRIES = 3\n\n"


def test_search_replace_tolerates_trailing_whitespace_and_indent_differences(
    tmp_path: Path,
) -> None:
    write_files(tmp_path, {"c.py": CONFIG})
    # the model dropped the indentation and added trailing spaces
    search = "if RETRIES:   \n    return TOKEN_TTL"
    code = "if RETRIES > 1:\n    return TOKEN_TTL * 2"
    assert apply(tmp_path, edit("search_replace", "c.py", code, search=search)) == []
    assert "    if RETRIES > 1:\n        return TOKEN_TTL * 2\n" in read(tmp_path, "c.py")
    check_parses(tmp_path, "c.py")


def test_search_replace_tolerant_match_with_deeper_model_indentation(tmp_path: Path) -> None:
    write_files(tmp_path, {"c.py": "def f():\n    a = 1\n    b = 2\n"})
    search = "        a = 1\n        b = 2"  # 8 spaces, the file has 4
    assert (
        apply(
            tmp_path,
            edit("search_replace", "c.py", "        a = 10\n        b = 20", search=search),
        )
        == []
    )
    assert read(tmp_path, "c.py") == "def f():\n    a = 10\n    b = 20\n"


def test_search_replace_zero_matches_quotes_the_closest_line(tmp_path: Path) -> None:
    write_files(tmp_path, {"app/config.py": CONFIG})
    errors = apply(
        tmp_path,
        edit("search_replace", "app/config.py", "TOKEN_TTL = 900", search="TOKEN_TTL = 61"),
    )
    assert errors[0].startswith(
        "EDIT 1 (search_replace path=app/config.py): SEARCH text not found."
    )
    assert "Closest line" in errors[0]
    assert errors[0].index('"TOKEN_TTL = 60  # seconds"') < errors[0].index("Copy the current code")
    assert "Copy the current code exactly, or use replace_symbol." in errors[0]


def test_search_replace_with_several_close_lines_quotes_up_to_three(tmp_path: Path) -> None:
    write_files(tmp_path, {"c.py": "value_a = 1\nvalue_b = 2\nvalue_c = 3\nvalue_d = 4\n"})
    errors = apply(tmp_path, edit("search_replace", "c.py", "y", search="value_e = 5"))
    assert "Closest lines:" in errors[0] and errors[0].count('"value_') == 3


def test_search_replace_many_matches_asks_for_more_context(tmp_path: Path) -> None:
    write_files(tmp_path, {"c.py": "a = 1\nb = 2\na = 1\nc = 3\na = 1\n"})
    errors = apply(tmp_path, edit("search_replace", "c.py", "a = 2", search="a = 1"))
    assert "matches 3 places (lines 1, 3, 5)" in errors[0]
    assert "more surrounding lines" in errors[0]
    assert read(tmp_path, "c.py") == "a = 1\nb = 2\na = 1\nc = 3\na = 1\n"


def test_search_replace_tolerant_matching_that_is_ambiguous_fails(tmp_path: Path) -> None:
    write_files(tmp_path, {"c.py": "def f():\n    x = 1\n\ndef g():\n    x = 1\n"})
    errors = apply(tmp_path, edit("search_replace", "c.py", "x = 2", search="x = 1  \n"))
    # exact text "x = 1  \n" is not in the file; tolerant matching finds two places
    assert "matches 2 places once spacing is ignored" in errors[0]


def test_search_replace_empty_search_is_refused(tmp_path: Path) -> None:
    write_files(tmp_path, {"c.py": CONFIG})
    assert (
        "SEARCH text is empty"
        in apply(tmp_path, edit("search_replace", "c.py", "x", search=" "))[0]
    )
    assert "SEARCH text is empty" in apply(tmp_path, edit("search_replace", "c.py", "x"))[0]


def test_search_replace_works_on_non_python_files(tmp_path: Path) -> None:
    write_files(tmp_path, {"README.md": "# Title\n\nHello world\n"})
    assert (
        apply(tmp_path, edit("search_replace", "README.md", "Hello there", search="Hello world"))
        == []
    )
    assert read(tmp_path, "README.md") == "# Title\n\nHello there\n"


# ------------------------------------------------------------------ create_file


def test_create_file_makes_parent_folders(tmp_path: Path) -> None:
    assert apply(tmp_path, edit("create_file", "pkg/sub/new_mod.py", "X = 1\n")) == []
    assert read(tmp_path, "pkg/sub/new_mod.py") == "X = 1\n"


def test_create_file_adds_a_missing_final_newline_and_allows_non_python(tmp_path: Path) -> None:
    assert apply(tmp_path, edit("create_file", "notes/readme.txt", "just text")) == []
    assert read(tmp_path, "notes/readme.txt") == "just text\n"


def test_create_file_on_an_existing_file_fails(mod: Path) -> None:
    before = read(mod, "app/mod.py")
    errors = apply(mod, edit("create_file", "app/mod.py", "X = 1\n"))
    assert "already exists" in errors[0] and "replace_symbol or search_replace" in errors[0]
    assert read(mod, "app/mod.py") == before


def test_create_file_python_must_parse(tmp_path: Path) -> None:
    errors = apply(tmp_path, edit("create_file", "bad.py", "def f(:\n"))
    assert "does not parse" in errors[0]
    assert not (tmp_path / "bad.py").exists()


def test_a_folder_in_the_way_is_reported(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    assert "is a folder" in apply(tmp_path, edit("create_file", "pkg", "x\n"))[0]


def test_editing_a_missing_file(tmp_path: Path) -> None:
    errors = apply(tmp_path, edit("replace_symbol", "nope.py", "def f(): pass\n", "f"))
    assert "does not exist" in errors[0] and "create_file" in errors[0]


def test_symbol_ops_need_python_files(tmp_path: Path) -> None:
    write_files(tmp_path, {"a.txt": "x\n"})
    assert (
        "only works on Python"
        in apply(tmp_path, edit("replace_symbol", "a.txt", "def f(): pass\n", "f"))[0]
    )


# ------------------------------------------------------------------ several edits in one reply


def test_edits_apply_in_order_and_see_each_other(mod: Path) -> None:
    errors = apply(
        mod,
        edit("replace_symbol", "app/mod.py", "def helper():\n    return 'v2'\n", "helper"),
        edit(
            "insert_after_symbol",
            "app/mod.py",
            "def helper_two():\n    return helper()\n",
            "helper",
        ),
        edit("create_file", "app/extra.py", "E = 1\n"),
        edit("search_replace", "app/extra.py", "E = 2", search="E = 1"),
        edit("add_import", "app/mod.py", "import json\n"),
    )
    assert errors == []
    text = read(mod, "app/mod.py")
    assert "return 'v2'" in text and "def helper_two" in text and "import json" in text
    assert read(mod, "app/extra.py") == "E = 2\n"
    check_parses(mod, "app/mod.py")


def test_two_edits_on_the_same_function_second_sees_the_first(mod: Path) -> None:
    errors = apply(
        mod,
        edit("replace_symbol", "app/mod.py", "def helper():\n    return 10\n", "helper"),
        edit("search_replace", "app/mod.py", "return 20", search="return 10"),
    )
    assert errors == [] and "return 20" in read(mod, "app/mod.py")


def test_if_any_edit_fails_nothing_is_written(mod: Path) -> None:
    before = read(mod, "app/mod.py")
    errors = apply(
        mod,
        edit("replace_symbol", "app/mod.py", "def helper():\n    return 99\n", "helper"),
        edit("create_file", "app/new.py", "N = 1\n"),
        edit("replace_symbol", "app/mod.py", "def nope(): pass\n", "nope"),
    )
    assert len(errors) == 1 and errors[0].startswith("EDIT 3 (replace_symbol")
    assert read(mod, "app/mod.py") == before
    assert not (mod / "app" / "new.py").exists()


def test_every_failing_edit_is_reported_with_its_number(mod: Path) -> None:
    errors = apply(
        mod,
        edit("replace_symbol", "app/mod.py", "def a(): pass\n", "missing_one"),
        edit("search_replace", "app/mod.py", "x", search="not in the file at all"),
    )
    assert [e.split(" ")[1] for e in errors] == ["1", "2"]


def test_no_edits_is_an_error(tmp_path: Path) -> None:
    assert apply_edits(tmp_path, [], "coder") == ["There are no edits to apply."]


def test_report_lists_changed_and_created_files(mod: Path) -> None:
    report = apply_edits_report(
        mod,
        [
            edit("search_replace", "app/mod.py", "return 2", search="return 1"),
            edit("create_file", "app/new.py", "N = 1\n"),
        ],
        "coder",
    )
    assert report.ok and report.changed == ["app/mod.py"] and report.created == ["app/new.py"]


# ------------------------------------------------------------------ path rules and protection


@pytest.mark.parametrize(
    ("path", "fragment"),
    [
        ("/etc/passwd", "relative"),
        ("C:/Windows/x.py", "relative"),
        ("../outside.py", "no .."),
        ("app/../../outside.py", "no .."),
        ("app\\mod.py", "forward slashes"),
        (".git/config", ".git/"),
        (".qed/last_apply.json", ".qed/"),
        (".GIT/hooks/pre-commit", ".GIT/"),
        ("", "empty"),
    ],
)
def test_bad_paths_are_refused(tmp_path: Path, path: str, fragment: str) -> None:
    errors = apply(tmp_path, edit("create_file", path, "X = 1\n"))
    assert "bad path" in errors[0] and fragment in errors[0]
    assert not (tmp_path.parent / "outside.py").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_symlink_that_leaves_the_project_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("S = 1\n")
    project = tmp_path / "project"
    project.mkdir()
    os.symlink(outside / "secret.py", project / "link.py")
    errors = apply(project, edit("search_replace", "link.py", "S = 2", search="S = 1"))
    assert "outside the project" in errors[0]
    assert (outside / "secret.py").read_text() == "S = 1\n"


def test_leading_dot_slash_is_tolerated(mod: Path) -> None:
    assert apply(mod, edit("search_replace", "./app/mod.py", "return 2", search="return 1")) == []


def test_protected_files_are_refused_for_coders(tmp_path: Path) -> None:
    write_files(
        tmp_path,
        {
            "tests/test_pages.py": "def test_a():\n    assert True\n",
            "pkg/test_inline.py": "X = 1\n",
            "conftest.py": "A = 1\n",
            "pyproject.toml": "[tool]\n",
            "src/code.py": "C = 1\n",
        },
    )
    for rel, search in (
        ("tests/test_pages.py", "True"),
        ("pkg/test_inline.py", "X = 1"),
        ("conftest.py", "A = 1"),
        ("pyproject.toml", "[tool]"),
    ):
        errors = apply(tmp_path, edit("search_replace", rel, "changed", search=search))
        assert "protected file" in errors[0], rel
    assert apply(tmp_path, edit("search_replace", "src/code.py", "C = 2", search="C = 1")) == []
    assert read(tmp_path, "tests/test_pages.py") == "def test_a():\n    assert True\n"
    # a coder may not create test files either
    assert (
        "protected file" in apply(tmp_path, edit("create_file", "tests/test_new.py", "X = 1\n"))[0]
    )
    # the repairer and the helper are coders too
    for role in ("repairer", "helper"):
        assert (
            "protected"
            in apply(
                tmp_path, edit("search_replace", "conftest.py", "B", search="A = 1"), role=role
            )[0]
        )


def test_protected_files_can_be_edited_when_the_task_allows_it(tmp_path: Path) -> None:
    write_files(tmp_path, {"tests/test_x.py": "def test_x():\n    assert 1 == 2\n"})
    errors = apply(
        tmp_path,
        edit("search_replace", "tests/test_x.py", "1 == 1", search="1 == 2"),
        allow_protected=True,
    )
    assert errors == [] and "1 == 1" in read(tmp_path, "tests/test_x.py")


def test_a_custom_protected_list_replaces_the_default(tmp_path: Path) -> None:
    write_files(tmp_path, {"tests/test_x.py": "A = 1\n", "docs/keep.md": "k\n"})
    assert (
        apply(
            tmp_path,
            edit("search_replace", "tests/test_x.py", "A = 2", search="A = 1"),
            protected=["docs/**"],
        )
        == []
    )
    assert (
        "protected"
        in apply(
            tmp_path, edit("search_replace", "docs/keep.md", "x", search="k"), protected=["docs/**"]
        )[0]
    )


def test_the_test_writer_may_only_create_new_test_files(tmp_path: Path) -> None:
    write_files(
        tmp_path,
        {"tests/test_old.py": "A = 1\n", "src/code.py": "C = 1\n", "pyproject.toml": "[t]\n"},
    )
    ok = apply(
        tmp_path,
        edit("create_file", "tests/test_new.py", "def test_n():\n    assert 1\n"),
        role="test_writer",
    )
    assert ok == [] and (tmp_path / "tests" / "test_new.py").exists()
    ok2 = apply(tmp_path, edit("create_file", "pkg/test_here.py", "X = 1\n"), role="test_writer")
    assert ok2 == []
    assert (
        "only create new test files"
        in apply(
            tmp_path,
            edit("search_replace", "tests/test_old.py", "A = 2", search="A = 1"),
            role="test_writer",
        )[0]
    )
    assert (
        "only create test files"
        in apply(tmp_path, edit("create_file", "src/new_code.py", "X = 1\n"), role="test_writer")[0]
    )
    assert (
        "only create test files"
        in apply(tmp_path, edit("create_file", "setup.cfg", "x\n"), role="test_writer")[0]
    )
    assert (
        "already exists"
        in apply(tmp_path, edit("create_file", "tests/test_old.py", "A = 5\n"), role="test_writer")[
            0
        ]
    )


# ------------------------------------------------------------------ line endings


def test_crlf_files_keep_crlf_after_symbol_and_text_edits(tmp_path: Path) -> None:
    crlf = MODULE.replace("\n", "\r\n")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "mod.py").write_bytes(crlf.encode("utf-8"))
    errors = apply(
        tmp_path,
        edit("replace_symbol", "app/mod.py", "def helper():\n    return 2\n", "helper"),
        edit("insert_after_symbol", "app/mod.py", "def added():\n    return 3\n", "helper"),
        edit("add_import", "app/mod.py", "import json\n"),
        edit("search_replace", "app/mod.py", '"ended"', search='"end"'),
    )
    assert errors == []
    data = (tmp_path / "app" / "mod.py").read_bytes()
    assert b"\r\n" in data
    assert data.replace(b"\r\n", b"").count(b"\n") == 0  # no lone LF anywhere
    assert (
        b"def helper():\r\n    return 2\r\n" in data
        and b'"ended"' in data
        and b"import json\r\n" in data
    )
    check_parses(tmp_path, "app/mod.py")


def test_lf_files_stay_lf_and_new_files_use_lf(tmp_path: Path) -> None:
    write_files(tmp_path, {"a.py": "A = 1\n"})
    assert (
        apply(
            tmp_path,
            edit("search_replace", "a.py", "A = 2", search="A = 1"),
            edit("create_file", "b.py", "B = 1\n"),
        )
        == []
    )
    assert b"\r" not in (tmp_path / "a.py").read_bytes()
    assert b"\r" not in (tmp_path / "b.py").read_bytes()


def test_utf8_bom_is_preserved(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_bytes(b"\xef\xbb\xbfA = 'h\xc3\xa9'\nB = 1\n")
    assert apply(tmp_path, edit("search_replace", "a.py", "B = 2", search="B = 1")) == []
    assert (tmp_path / "a.py").read_bytes() == b"\xef\xbb\xbfA = 'h\xc3\xa9'\nB = 2\n"


def test_non_utf8_files_are_refused(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_bytes(b"A = '\xff\xfe'\n")
    assert "not a UTF-8" in apply(tmp_path, edit("search_replace", "a.py", "B", search="A"))[0]


# ------------------------------------------------------------------ helpers


def test_list_symbols() -> None:
    assert list_symbols(MODULE) == [
        "helper",
        "decorated",
        "Store",
        "Store.rotate",
        "Store.issue",
        "Store.Inner",
        "Store.Inner.deep",
        "fetch",
        "last",
    ]
    assert list_symbols("def broken(:") == []


# ------------------------------------------------------------------ the bug zoo


@pytest.mark.parametrize("name", zoo_project_names())
def test_every_bug_zoo_edits_file_applies_and_makes_the_hidden_tests_pass(
    check_zoo, zoo_copy, name: str
) -> None:
    project = zoo_copy(name)
    assert check_zoo.run_pytest(project, "hidden/test_hidden.py") != 0  # fails before
    reply = parse_reply((project / "edits.txt").read_text(encoding="utf-8"))
    assert reply_ok(reply), reply.errors
    errors = apply_edits(project, reply.edits, "coder")
    assert errors == [], errors
    assert check_zoo.run_pytest(project, "hidden/test_hidden.py") == 0  # passes after
    assert check_zoo.run_pytest(project) == 0  # and the visible tests still pass
