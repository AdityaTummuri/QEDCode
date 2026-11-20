"""JavaScript and TypeScript symbol edits with tree-sitter (AGENTS.md section 15.3).

The same three symbol operations as the Python applier (replace_symbol, insert_after_symbol,
add_import), located by tree-sitter nodes: line numbers are never taken from the model. Every
function raises `JsEditError` with a message written for the model (what, why, how to fix it).
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from pathlib import Path

import tree_sitter_javascript
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

EXTENSIONS = {
    ".js": "js",
    ".jsx": "js",
    ".mjs": "js",
    ".cjs": "js",
    ".ts": "ts",
    ".mts": "ts",
    ".cts": "ts",
    ".tsx": "tsx",
}
MAX_SYMBOLS_LISTED = 40
_TOP_DECLARATIONS = {
    "function_declaration",
    "generator_function_declaration",
    "class_declaration",
    "abstract_class_declaration",
    "interface_declaration",
    "type_alias_declaration",
    "enum_declaration",
}
_VARIABLE_DECLARATIONS = {"lexical_declaration", "variable_declaration"}
_MEMBERS = {"method_definition", "public_field_definition", "field_definition"}
_LANGUAGES: dict[str, Language] = {}


class JsEditError(Exception):
    """A JavaScript/TypeScript edit could not be applied; the message is shown to the model."""


def language_for(path: str) -> str | None:
    """'js', 'ts' or 'tsx' for a JavaScript/TypeScript file name, else None."""
    return EXTENSIONS.get(Path(path).suffix.lower())


def is_js_file(path: str) -> bool:
    return language_for(path) is not None


def _language(lang: str) -> Language:
    if lang not in _LANGUAGES:
        pointer = {
            "js": tree_sitter_javascript.language,
            "ts": tree_sitter_typescript.language_typescript,
            "tsx": tree_sitter_typescript.language_tsx,
        }[lang]()
        _LANGUAGES[lang] = Language(pointer)
    return _LANGUAGES[lang]


def parse(source: bytes | str, lang: str) -> Node:
    """The syntax tree root of `source` (always returns a tree; check `syntax_error`)."""
    data = source.encode("utf-8") if isinstance(source, str) else source
    return Parser(_language(lang)).parse(data).root_node


def _first_error(node: Node) -> Node | None:
    if not node.has_error:
        return None
    if node.is_error or node.is_missing:
        return node
    for child in node.children:
        found = _first_error(child)
        if found is not None:
            return found
    return node if node.is_error else None


def syntax_error(source: bytes | str, lang: str) -> str | None:
    """'line N: <what is wrong>' for the first syntax error, or None when the code parses."""
    root = parse(source, lang)
    if not root.has_error:
        return None
    node = _first_error(root) or root
    line = node.start_point.row + 1
    if node.is_missing:
        return f"line {line}: missing {node.type!r}"
    snippet = (node.text or b"").decode("utf-8", "replace").strip().splitlines()
    shown = (snippet[0] if snippet else "")[:30]
    return f"line {line}: unexpected {shown!r}" if shown else f"line {line}: syntax error"


# --------------------------------------------------------------------------- symbols


@dataclass
class Symbol:
    dotted: str
    outer: Node  # the whole statement (with `export`) or the class member
    declaration: Node  # the node after any `export` keyword
    shared: bool = False  # declared together with other variables in one statement
    member: bool = False  # a method or field of a class


def _name_of(node: Node) -> str | None:
    name = node.child_by_field_name("name")
    if name is None:
        return None
    if name.type in ("computed_property_name", "string", "number"):
        return None
    return (name.text or b"").decode("utf-8")


def _members(class_node: Node, prefix: str) -> list[Symbol]:
    body = class_node.child_by_field_name("body")
    found: list[Symbol] = []
    for child in body.children if body is not None else []:
        if child.type not in _MEMBERS:
            continue
        name = _name_of(child) or _property_name(child)
        if name:
            found.append(Symbol(f"{prefix}.{name}", child, child, member=True))
    return found


def _property_name(node: Node) -> str | None:
    prop = node.child_by_field_name("property")
    return (prop.text or b"").decode("utf-8") if prop is not None else None


def symbols_of(root: Node) -> list[Symbol]:
    """Every top-level function, class (with its members), type and variable, in file order."""
    found: list[Symbol] = []
    for child in root.children:
        declaration = child
        if child.type == "export_statement":
            inner = child.child_by_field_name("declaration")
            if inner is None:
                continue
            declaration = inner
        if declaration.type in _TOP_DECLARATIONS:
            name = _name_of(declaration)
            if name:
                found.append(Symbol(name, child, declaration))
                if declaration.type in ("class_declaration", "abstract_class_declaration"):
                    found.extend(_members(declaration, name))
        elif declaration.type in _VARIABLE_DECLARATIONS:
            declarators = [c for c in declaration.children if c.type == "variable_declarator"]
            for declarator in declarators:
                name_node = declarator.child_by_field_name("name")
                if name_node is not None and name_node.type == "identifier":
                    text = (name_node.text or b"").decode("utf-8")
                    found.append(Symbol(text, child, declaration, shared=len(declarators) > 1))
    return found


def list_symbols(source: str, lang: str) -> list[str]:
    """Dotted names of the symbols in `source` (for error hints); [] when it does not parse."""
    root = parse(source, lang)
    return [] if root.has_error else [s.dotted for s in symbols_of(root)]


def _locate(rel: str, source: str, lang: str, symbol: str) -> tuple[Node, Symbol]:
    root = parse(source, lang)
    problem = syntax_error(source, lang)
    if problem:
        raise JsEditError(
            f"{rel} has a syntax error ({problem}), so its symbols cannot be located. "
            "Use search_replace to fix that part first."
        )
    matches = [s for s in symbols_of(root) if s.dotted == symbol]
    if not matches:
        names = [s.dotted for s in symbols_of(root)]
        shown = ", ".join(names[:MAX_SYMBOLS_LISTED]) or "none"
        more = " ..." if len(names) > MAX_SYMBOLS_LISTED else ""
        raise JsEditError(f"symbol not found.\nSymbols in this file: {shown}{more}.")
    if len(matches) > 1:
        lines = ", ".join(str(m.outer.start_point.row + 1) for m in matches)
        raise JsEditError(
            f"the symbol '{symbol}' is defined {len(matches)} times (lines {lines}), so it is "
            "ambiguous. Use search_replace with enough surrounding lines for this one."
        )
    return root, matches[0]


# --------------------------------------------------------------------------- new code


def _dedent(code: str) -> str:
    lines = textwrap.dedent(code.rstrip("\n")).split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def _parse_new_code(code: str, lang: str, member: bool) -> tuple[str, list[str]]:
    """Dedent new code, check that it parses on its own, return it with the names it defines."""
    text = _dedent(code)
    if not text:
        raise JsEditError("the new code is empty. Send the complete new definition.")
    if member:
        wrapped = f"class __Qed {{\n{text}\n}}"
        problem = syntax_error(wrapped, lang)
        if problem:
            raise JsEditError(
                f"the new class member does not parse ({_shift_line(problem, -1)}). "
                "Send one complete method or field, with its braces."
            )
        root = parse(wrapped, lang)
        classes = [s for s in symbols_of(root) if s.dotted.startswith("__Qed.")]
        return text, [s.dotted.split(".", 1)[1] for s in classes]
    problem = syntax_error(text, lang)
    if problem:
        raise JsEditError(
            f"the new code does not parse on its own ({problem}). Send complete code with its "
            "brackets and braces."
        )
    return text, [s.dotted for s in symbols_of(parse(text, lang))]


def _shift_line(problem: str, by: int) -> str:
    """'line 3: ...' with the number moved by `by` (code that was wrapped in a header line)."""
    match = re.match(r"line (\d+)(.*)", problem)
    return f"line {max(int(match.group(1)) + by, 1)}{match.group(2)}" if match else problem


def _indent_of(source: str, node: Node) -> str:
    line = source.split("\n")[node.start_point.row]
    return line[: len(line) - len(line.lstrip(" \t"))]


def _indented(text: str, indent: str) -> str:
    """Indent every line but the first (the first sits where the old code started)."""
    lines = text.split("\n")
    return "\n".join([lines[0], *[(indent + ln) if ln.strip() else ln for ln in lines[1:]]])


def _splice(source: str, start: int, end: int, replacement: str) -> str:
    data = source.encode("utf-8")
    return (data[:start] + replacement.encode("utf-8") + data[end:]).decode("utf-8")


def _check_result(rel: str, text: str, lang: str) -> None:
    problem = syntax_error(text, lang)
    if problem:
        raise JsEditError(
            f"after this edit {rel} would have a syntax error ({problem}). "
            "Check the brackets, braces and commas of the code you sent."
        )


def _export_prefix(symbol: Symbol, source: bytes) -> str:
    """'export ' or 'export default ' when the symbol is exported, else ''."""
    if symbol.outer.type != "export_statement":
        return ""
    return source[symbol.outer.start_byte : symbol.declaration.start_byte].decode("utf-8")


# --------------------------------------------------------------------------- the three ops


def replace_symbol(rel: str, source: str, symbol: str, code: str) -> str:
    lang = language_for(rel) or "js"
    _root, target = _locate(rel, source, lang, symbol)
    if target.shared:
        raise JsEditError(
            f"'{symbol}' is declared together with other variables in one statement, so it "
            "cannot be replaced alone. Use search_replace."
        )
    leaf = symbol.split(".")[-1]
    new_code, defined = _parse_new_code(code, lang, target.member)
    # a new top-level definition may carry its own `export`: its symbols are found through it
    if leaf not in defined:
        found = ", ".join(defined) if defined else "no function, class or variable"
        raise JsEditError(
            f"the new code defines {found}, but the symbol being replaced is '{leaf}'. "
            "A replacement must keep the name. To add a new function or class, use "
            "insert_after_symbol instead."
        )
    data = source.encode("utf-8")
    if not target.member and not new_code.lstrip().startswith("export"):
        new_code = _export_prefix(target, data) + new_code  # keep the original export keyword
    indent = _indent_of(source, target.outer)
    result = _splice(
        source,
        target.outer.start_byte,
        target.outer.end_byte,
        _indented(new_code, indent),
    )
    _check_result(rel, result, lang)
    return result


def insert_after_symbol(rel: str, source: str, symbol: str, code: str) -> str:
    lang = language_for(rel) or "js"
    _root, target = _locate(rel, source, lang, symbol)
    new_code, _defined = _parse_new_code(code, lang, target.member)
    indent = _indent_of(source, target.outer)
    addition = "\n\n" + indent + _indented(new_code, indent)
    result = _splice(source, target.outer.end_byte, target.outer.end_byte, addition)
    if not result.endswith("\n"):
        result += "\n"
    _check_result(rel, result, lang)
    return result


def _normalize_import(text: str) -> str:
    """The same import written with other spacing, quotes or a semicolon compares equal."""
    return re.sub(r"\s+", "", text).replace("'", '"').rstrip(";")


def add_import(rel: str, source: str, code: str) -> str:
    lang = language_for(rel) or "js"
    text = _dedent(code)
    problem = syntax_error(text, lang) if text else "no lines"
    if problem:
        raise JsEditError(
            f"the import lines do not parse ({problem}). "
            'Send only lines like `import fs from "fs";` or `import { a } from "./a.js";`.'
        )
    wanted = [c for c in parse(text, lang).children if c.type != "comment"]
    if not wanted or any(c.type != "import_statement" for c in wanted):
        raise JsEditError(
            "add_import accepts only `import ... from ...` lines. For `require(...)` or other "
            "code use insert_after_symbol or search_replace."
        )
    problem = syntax_error(source, lang)
    if problem:
        raise JsEditError(
            f"{rel} has a syntax error ({problem}), so imports cannot be placed. "
            "Use search_replace to fix that part first."
        )
    root = parse(source, lang)
    existing = [c for c in root.children if c.type == "import_statement"]
    present = {_normalize_import((c.text or b"").decode("utf-8")) for c in existing}
    new_lines = []
    for node in wanted:
        line = (node.text or b"").decode("utf-8").strip()
        if _normalize_import(line) not in present:
            present.add(_normalize_import(line))
            new_lines.append(line)
    if not new_lines:
        return source
    block = "\n".join(new_lines)
    if existing:
        last = existing[-1]
        return _splice(source, last.end_byte, last.end_byte, "\n" + block)
    at = _after_header(root)
    prefix = source.encode("utf-8")[:at].decode("utf-8")
    rest = source.encode("utf-8")[at:].decode("utf-8")
    glue_before = (
        "" if not prefix or prefix.endswith("\n\n") else ("\n" if prefix.endswith("\n") else "\n\n")
    )
    glue_after = "\n\n" if rest.strip() else "\n"
    return prefix + glue_before + block + glue_after + rest.lstrip("\n")


def _after_header(root: Node) -> int:
    """Byte offset after a shebang, leading comments and a 'use strict' directive."""
    at = 0
    for child in root.children:
        directive = child.type == "expression_statement" and child.children[0].type == "string"
        if child.type in ("comment", "hash_bang_line") or directive:
            at = child.end_byte
        else:
            break
    return at
