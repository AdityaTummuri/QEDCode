"""JavaScript and TypeScript symbol edits (AGENTS.md section 15.3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from qedcode.edits import js_symbols as js
from qedcode.edits.apply import apply_edits
from qedcode.models.schemas import EditOp

JS = """import fs from "fs";
import { a, b } from "./x.js";

export function add(a, b) {
  return a - b;
}

export default class Cart {
  total() {
    return 1;
  }

  static make() {
    return new Cart();
  }
}

const sub = (a, b) => a - b;
export const mul = function (a, b) {
  return a * b;
};
let counter = 0, other = 1;
module.exports = { add };
"""

TS = """import type { Foo } from "./foo";

export interface Item {
  id: number;
}

type Id = string | number;

export enum Color {
  Red,
  Green,
}

export abstract class Repo<T> {
  abstract find(id: Id): T;
  count(): number {
    return 0;
  }
}

function over(a: string): string;
function over(a: number): number;
function over(a: any) {
  return a;
}

export async function load(id: Id): Promise<Item> {
  return { id: 1 };
}
"""


def edit(op: str, path: str, code: str = "", symbol: str | None = None, search: str | None = None):
    return EditOp(op=op, path=path, symbol=symbol, search=search, code=code)  # type: ignore[arg-type]


# ------------------------------------------------------------------ parsing


@pytest.mark.parametrize(
    ("name", "lang"),
    [
        ("a.js", "js"), ("a.jsx", "js"), ("a.mjs", "js"), ("a.cjs", "js"), ("a.ts", "ts"),
        ("a.mts", "ts"), ("a.cts", "ts"), ("a.tsx", "tsx"), ("dir/A.TS", "ts"),
    ],
)  # fmt: skip
def test_language_by_extension(name: str, lang: str) -> None:
    assert js.language_for(name) == lang and js.is_js_file(name)


def test_other_files_are_not_javascript() -> None:
    assert js.language_for("a.py") is None and js.language_for("README.md") is None
    assert not js.is_js_file("package.json")


def test_good_code_has_no_syntax_error() -> None:
    assert js.syntax_error(JS, "js") is None and js.syntax_error(TS, "ts") is None
    assert js.syntax_error("const el = <div className='x'>{1}</div>;", "tsx") is None
    assert js.syntax_error("const el = <div>{1}</div>;", "js") is None  # JSX in plain JS


def test_syntax_errors_name_the_line() -> None:
    missing = js.syntax_error("function f() {\n  return 1;\n", "js")
    assert missing is not None and missing.startswith("line ") and "missing" in missing
    unexpected = js.syntax_error("const x = 1;\nconst y = ;\n", "js")
    assert unexpected is not None and unexpected.startswith("line 2")
    assert js.syntax_error("let x: number = 1;", "js") is not None  # types are not JavaScript
    assert js.syntax_error("let x: number = 1;", "ts") is None


def test_symbols_of_javascript_and_typescript() -> None:
    assert js.list_symbols(JS, "js") == [
        "add", "Cart", "Cart.total", "Cart.make", "sub", "mul", "counter", "other",
    ]  # fmt: skip
    assert js.list_symbols(TS, "ts") == [
        "Item", "Id", "Color", "Repo", "Repo.count", "over", "load",
    ]  # fmt: skip
    assert js.list_symbols("function f( {", "js") == []


# ------------------------------------------------------------------ replace_symbol


def test_replace_a_function_keeps_export() -> None:
    new = "function add(a, b) {\n  return a + b;\n}"
    result = js.replace_symbol("m.js", JS, "add", new)
    assert "export function add(a, b) {\n  return a + b;\n}" in result
    assert "return a - b" not in result and js.syntax_error(result, "js") is None
    assert result.count("export function add") == 1


def test_replace_with_its_own_export_is_not_doubled() -> None:
    result = js.replace_symbol("m.js", JS, "add", "export function add(a, b) {\n  return 0;\n}")
    assert result.count("export function add") == 1 and "export export" not in result


def test_replace_an_exported_default_class_keeps_export_default() -> None:
    new = "class Cart {\n  total() {\n    return 2;\n  }\n}"
    result = js.replace_symbol("m.js", JS, "Cart", new)
    assert "export default class Cart {\n  total() {\n    return 2;\n  }\n}" in result
    assert "static make" not in result and js.syntax_error(result, "js") is None


def test_replace_an_arrow_function_constant() -> None:
    result = js.replace_symbol("m.js", JS, "sub", "const sub = (a, b) => b - a;")
    assert "const sub = (a, b) => b - a;" in result and "a - b;\nexport const mul" not in result
    assert "export const mul" in result


def test_replace_a_class_method_keeps_the_indentation() -> None:
    result = js.replace_symbol("m.js", JS, "Cart.total", "total() {\n  return 42;\n}")
    assert "  total() {\n    return 42;\n  }\n" in result
    assert "static make()" in result and js.syntax_error(result, "js") is None


def test_replace_in_typescript() -> None:
    result = js.replace_symbol(
        "m.ts", TS, "Item", "interface Item {\n  id: number;\n  name: string;\n}"
    )
    assert "export interface Item {\n  id: number;\n  name: string;\n}" in result
    result = js.replace_symbol("m.ts", TS, "Color", "enum Color {\n  Red,\n  Blue,\n}")
    assert "export enum Color" in result and "Blue" in result and "Green" not in result
    result = js.replace_symbol(
        "m.ts", TS, "load", "async function load(id: Id): Promise<Item> {\n  return { id };\n}"
    )
    assert "export async function load" in result and "return { id };" in result
    result = js.replace_symbol("m.ts", TS, "Repo.count", "count(): number {\n  return 1;\n}")
    assert "  count(): number {\n    return 1;\n  }" in result and "abstract find" in result


def test_replace_an_overloaded_function_replaces_the_implementation_only() -> None:
    result = js.replace_symbol("m.ts", TS, "over", "function over(a: any) {\n  return [a];\n}")
    assert result.count("function over(a:") == 3 and "return [a];" in result
    assert "function over(a: string): string;" in result


def test_replace_errors_help_the_model() -> None:
    with pytest.raises(
        js.JsEditError, match=r"symbol not found\.\nSymbols in this file: add, Cart"
    ):
        js.replace_symbol("m.js", JS, "nope", "function nope() {}")
    with pytest.raises(js.JsEditError, match="must keep the name"):
        js.replace_symbol("m.js", JS, "add", "function other() {}")
    with pytest.raises(js.JsEditError, match="does not parse on its own"):
        js.replace_symbol("m.js", JS, "add", "function add(a, b) {")
    with pytest.raises(js.JsEditError, match="declared together with other variables"):
        js.replace_symbol("m.js", JS, "counter", "let counter = 5;")
    with pytest.raises(js.JsEditError, match="is empty"):
        js.replace_symbol("m.js", JS, "add", "\n\n")
    with pytest.raises(js.JsEditError, match="syntax error"):
        js.replace_symbol("m.js", "function f( {", "f", "function f() {}")


def test_a_class_member_must_be_one_complete_member() -> None:
    with pytest.raises(js.JsEditError, match=r"class member does not parse \(line \d+"):
        js.replace_symbol("m.js", JS, "Cart.total", "total() { return 1;")
    with pytest.raises(js.JsEditError, match="must keep the name"):
        js.replace_symbol("m.js", JS, "Cart.total", "other() {\n  return 1;\n}")


def test_an_ambiguous_symbol_is_refused() -> None:
    source = "function dup() {\n  return 1;\n}\n\nfunction dup() {\n  return 2;\n}\n"
    with pytest.raises(js.JsEditError, match=r"defined 2 times \(lines 1, 5\)"):
        js.replace_symbol("m.js", source, "dup", "function dup() {}")


def test_non_ascii_text_survives_an_edit() -> None:
    source = 'const greeting = "héllo wörld ✓";\n\nfunction f() {\n  return 1;\n}\n'
    result = js.replace_symbol("m.js", source, "f", "function f() {\n  return 2;\n}")
    assert 'const greeting = "héllo wörld ✓";' in result and "return 2;" in result


# ------------------------------------------------------------------ insert_after_symbol


def test_insert_a_function_after_a_function() -> None:
    result = js.insert_after_symbol("m.js", JS, "add", "function neg(a) {\n  return -a;\n}")
    assert "}\n\nfunction neg(a) {\n  return -a;\n}\n\nexport default class Cart" in result
    assert js.syntax_error(result, "js") is None


def test_insert_a_method_after_a_method_is_indented() -> None:
    result = js.insert_after_symbol("m.js", JS, "Cart.total", "reset() {\n  return 0;\n}")
    assert "    return 1;\n  }\n\n  reset() {\n    return 0;\n  }\n" in result
    assert js.list_symbols(result, "js")[2:5] == ["Cart.total", "Cart.reset", "Cart.make"]


def test_insert_typescript_declarations() -> None:
    result = js.insert_after_symbol("m.ts", TS, "Item", "interface Extra {\n  tag: string;\n}")
    assert js.list_symbols(result, "ts")[:3] == ["Item", "Extra", "Id"]


def test_insert_at_the_end_of_a_file_without_a_trailing_newline() -> None:
    result = js.insert_after_symbol("m.js", "function a() {}", "a", "function b() {}")
    assert result == "function a() {}\n\nfunction b() {}\n"


def test_insert_errors() -> None:
    with pytest.raises(js.JsEditError, match="symbol not found"):
        js.insert_after_symbol("m.js", JS, "zzz", "function z() {}")
    with pytest.raises(js.JsEditError, match="does not parse on its own"):
        js.insert_after_symbol("m.js", JS, "add", "function z( {")


# ------------------------------------------------------------------ add_import


def test_add_import_goes_after_the_last_import() -> None:
    result = js.add_import("m.js", JS, 'import path from "path";')
    assert 'import { a, b } from "./x.js";\nimport path from "path";\n' in result


def test_add_import_skips_what_is_already_there() -> None:
    assert js.add_import("m.js", JS, "import fs from 'fs'") == JS  # quotes and semicolon differ
    assert js.add_import("m.js", JS, 'import {a,b} from "./x.js";') == JS
    result = js.add_import("m.js", JS, 'import fs from "fs";\nimport os from "os";')
    assert result.count("import fs") == 1 and 'import os from "os";' in result


def test_add_import_into_a_file_without_imports() -> None:
    result = js.add_import("m.js", "const x = 1;\n", 'import fs from "fs";')
    assert result == 'import fs from "fs";\n\nconst x = 1;\n'
    header = '#!/usr/bin/env node\n"use strict";\n// about\nconst x = 1;\n'
    placed = js.add_import("m.js", header, 'import fs from "fs";')
    assert placed.startswith('#!/usr/bin/env node\n"use strict";\n// about\n\nimport fs from "fs";')
    assert placed.endswith("const x = 1;\n")
    assert js.add_import("m.js", "", 'import fs from "fs";') == 'import fs from "fs";\n'


def test_add_import_in_typescript_with_type_imports() -> None:
    result = js.add_import("m.ts", TS, 'import type { Bar } from "./bar";')
    assert 'import type { Foo } from "./foo";\nimport type { Bar } from "./bar";\n' in result


def test_add_import_refuses_other_code() -> None:
    with pytest.raises(js.JsEditError, match="only `import"):
        js.add_import("m.js", JS, 'const fs = require("fs");')
    with pytest.raises(js.JsEditError, match="only `import"):
        js.add_import("m.js", JS, 'import fs from "fs";\nconsole.log(1);')
    with pytest.raises(js.JsEditError, match="do not parse"):
        js.add_import("m.js", JS, "import from")


# ------------------------------------------------------------------ through the applier


def put(folder: Path, **files: str) -> None:
    for name, text in files.items():
        (folder / name.replace("__", "/").replace("_dot_", ".")).parent.mkdir(
            parents=True, exist_ok=True
        )
        (folder / name.replace("__", "/").replace("_dot_", ".")).write_text(text)


def test_the_applier_edits_javascript_and_typescript_files(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.js").write_text(JS)
    (tmp_path / "src" / "m.ts").write_text(TS)
    errors = apply_edits(
        tmp_path,
        [
            edit("replace_symbol", "src/m.js", "function add(a, b) {\n  return a + b;\n}", "add"),
            edit("insert_after_symbol", "src/m.js", "function neg(a) {\n  return -a;\n}", "add"),
            edit("add_import", "src/m.js", 'import path from "path";'),
            edit("replace_symbol", "src/m.ts", "interface Item {\n  id: string;\n}", "Item"),
            edit(
                "search_replace",
                "src/m.ts",
                "type Id = string;",
                search="type Id = string | number;",
            ),
        ],
        "coder",
    )
    assert errors == []
    js_text = (tmp_path / "src" / "m.js").read_text()
    assert (
        "return a + b;" in js_text
        and "function neg" in js_text
        and 'import path from "path"' in js_text
    )
    ts_text = (tmp_path / "src" / "m.ts").read_text()
    assert "id: string;" in ts_text and "type Id = string;" in ts_text


def test_a_failed_javascript_edit_writes_nothing_and_explains(tmp_path: Path) -> None:
    (tmp_path / "m.js").write_text(JS)
    errors = apply_edits(
        tmp_path,
        [
            edit("replace_symbol", "m.js", "function add(a, b) {\n  return 0;\n}", "add"),
            edit("replace_symbol", "m.js", "function nope() {}", "nope"),
        ],
        "coder",
    )
    assert (
        len(errors) == 1 and "symbol not found" in errors[0] and "Symbols in this file" in errors[0]
    )
    assert (tmp_path / "m.js").read_text() == JS  # the first edit was not written either


def test_symbol_edits_still_refuse_other_file_types(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("x\n")
    errors = apply_edits(
        tmp_path, [edit("add_import", "notes.txt", 'import fs from "fs";')], "coder"
    )
    assert "only works on Python, JavaScript and TypeScript" in errors[0]
    assert "search_replace" in errors[0]


def test_crlf_files_keep_their_line_endings(tmp_path: Path) -> None:
    (tmp_path / "m.js").write_bytes(JS.replace("\n", "\r\n").encode())
    errors = apply_edits(
        tmp_path,
        [edit("replace_symbol", "m.js", "function add(a, b) {\n  return a + b;\n}", "add")],
        "coder",
    )
    data = (tmp_path / "m.js").read_bytes()
    assert errors == [] and b"return a + b;\r\n" in data and b"\r\n" in data
    assert data.count(b"\n") == data.count(b"\r\n")
