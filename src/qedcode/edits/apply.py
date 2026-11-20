"""The strict, AST-based edit applier (AGENTS.md section 7).

`apply_edits` applies a reply's edits in order to one worktree. If any edit fails, NOTHING is
written and the errors name the edit, the problem and the way out, in words a small model can act
on. Python's `ast` (lineno, end_lineno, col_offset) locates symbols; no line numbers are ever
taken from the model.
"""

from __future__ import annotations

import ast
import difflib
import io
import tokenize
from dataclasses import dataclass, field
from pathlib import Path

from qedcode.config import DEFAULT_PROTECTED
from qedcode.edits import js_symbols
from qedcode.models.schemas import EditOp
from qedcode.project.paths import is_protected, is_test_file, normalize_path, validate_path

MAX_SYMBOLS_LISTED = 40
_DEF_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


@dataclass
class ApplyReport:
    """The outcome of applying a reply: errors (empty = success) and which files were touched."""

    errors: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class _EditError(Exception):
    """One edit could not be applied; the message is shown to the model."""


# --------------------------------------------------------------------------- virtual files


@dataclass
class _File:
    text: str  # always LF inside; written back with CRLF when the original used it
    crlf: bool = False
    bom: bool = False
    exists_on_disk: bool = True
    dirty: bool = False


class _Files:
    """The worktree as the reply sees it: reads from disk, keeps edits in memory until the end."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.files: dict[str, _File | None] = {}

    def get(self, rel: str) -> _File | None:
        if rel in self.files:
            return self.files[rel]
        path = self.root / rel
        loaded: _File | None = None
        if path.is_dir():
            raise _EditError(f"{rel} is a folder, not a file.")
        if path.is_file():
            data = path.read_bytes()
            bom = data.startswith(b"\xef\xbb\xbf")
            try:
                text = data[3:].decode("utf-8") if bom else data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise _EditError(
                    f"{rel} is not a UTF-8 text file ({exc.reason}), so it cannot be edited."
                ) from None
            crlf = b"\r\n" in data and data.count(b"\r\n") * 2 >= data.count(b"\n")
            loaded = _File(text.replace("\r\n", "\n"), crlf, bom)
        self.files[rel] = loaded
        return loaded

    def put(self, rel: str, file: _File) -> None:
        file.dirty = True
        self.files[rel] = file

    def write_all(self) -> tuple[list[str], list[str]]:
        changed: list[str] = []
        created: list[str] = []
        for rel, file in self.files.items():
            if file is None or not file.dirty:
                continue
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            text = file.text.replace("\n", "\r\n") if file.crlf else file.text
            data = text.encode("utf-8")
            target.write_bytes((b"\xef\xbb\xbf" + data) if file.bom else data)
            (changed if file.exists_on_disk else created).append(rel)
        return sorted(changed), sorted(created)


# --------------------------------------------------------------------------- text helpers


def _leading_ws(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def _common_prefix(prefixes: list[str]) -> str:
    if not prefixes:
        return ""
    first, last = min(prefixes), max(prefixes)
    for i, ch in enumerate(first):
        if ch != last[i]:
            return first[:i]
    return first


def _interior_lines(code: str) -> set[int]:
    """0-based indexes of lines that begin inside a multi-line string (their text is data)."""
    interior: set[int] = set()
    fstart = getattr(tokenize, "FSTRING_START", None)
    fend = getattr(tokenize, "FSTRING_END", None)
    open_rows: list[int] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(code).readline):
            if tok.type == tokenize.STRING and tok.end[0] > tok.start[0]:
                interior.update(range(tok.start[0], tok.end[0]))
            elif fstart is not None and tok.type == fstart:
                open_rows.append(tok.start[0])
            elif fend is not None and tok.type == fend and open_rows:
                start_row = open_rows.pop()
                if tok.end[0] > start_row:
                    interior.update(range(start_row, tok.end[0]))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return set()
    return interior


def dedent_code(code: str) -> str:
    """Remove the common indentation, leaving the inside of multi-line strings alone."""
    lines = code.split("\n")
    interior = _interior_lines(code) or _interior_lines(_plain_dedent(code))
    margin = _common_prefix(
        [_leading_ws(ln) for i, ln in enumerate(lines) if ln.strip() and i not in interior]
    )
    out = []
    for i, ln in enumerate(lines):
        if not ln.strip() and i not in interior:
            out.append("")
        elif i in interior or not margin:
            out.append(ln)
        else:
            out.append(ln[len(margin) :] if ln.startswith(margin) else ln.lstrip(" \t"))
    return "\n".join(out)


def _plain_dedent(code: str) -> str:
    lines = code.split("\n")
    margin = _common_prefix([_leading_ws(ln) for ln in lines if ln.strip()])
    return "\n".join(ln[len(margin) :] if ln.strip() else "" for ln in lines)


def indent_code(code: str, indent: str) -> list[str]:
    """Indent code lines by `indent`, except blank lines and the inside of multi-line strings."""
    if not indent:
        return code.split("\n")
    lines = code.split("\n")
    interior = _interior_lines(code)
    return [ln if (not ln.strip() or i in interior) else indent + ln for i, ln in enumerate(lines)]


def _trim_blank_edges(lines: list[str]) -> list[str]:
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


# --------------------------------------------------------------------------- symbols


def _children(node: ast.AST) -> list[ast.AST]:
    return list(getattr(node, "body", []))


def list_symbols(source: str) -> list[str]:
    """Dotted names of every function, class and method defined in `source` (for error hints)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    names: list[str] = []

    def walk(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, _DEF_TYPES):
                dotted = f"{prefix}{node.name}"
                names.append(dotted)
                if isinstance(node, ast.ClassDef):
                    walk(node.body, dotted + ".")

    walk(tree.body, "")
    return names


def find_symbol(tree: ast.Module, dotted: str) -> list[ast.AST]:
    """All definitions matching `Class.method` style paths (several means ambiguous)."""
    parts = dotted.split(".")
    scope: list[ast.AST] = [tree]
    for depth, part in enumerate(parts):
        matches = [
            child
            for owner in scope
            for child in _children(owner)
            if isinstance(child, _DEF_TYPES) and child.name == part
        ]
        if depth < len(parts) - 1:
            scope = [m for m in matches if isinstance(m, ast.ClassDef)]
            if not scope:
                return []
        else:
            return matches
    return []


def _node_span(node: ast.AST) -> tuple[int, int]:
    """0-based inclusive (first, last) line indexes, from the first decorator to end_lineno."""
    first = node.lineno  # type: ignore[attr-defined]
    for deco in getattr(node, "decorator_list", []):
        first = min(first, deco.lineno)
    return first - 1, node.end_lineno - 1  # type: ignore[attr-defined]


def _parse(rel: str, text: str) -> ast.Module:
    try:
        return ast.parse(text)
    except SyntaxError as exc:
        raise _EditError(
            f"{rel} has a syntax error on line {exc.lineno} ({exc.msg}), so its symbols cannot be "
            "located. Use search_replace to fix that part first."
        ) from None


def _locate(rel: str, text: str, symbol: str) -> tuple[ast.AST, list[str]]:
    tree = _parse(rel, text)
    matches = find_symbol(tree, symbol)
    if not matches:
        names = list_symbols(text)
        shown = ", ".join(names[:MAX_SYMBOLS_LISTED]) or "none"
        more = " ..." if len(names) > MAX_SYMBOLS_LISTED else ""
        raise _EditError(f"symbol not found.\nSymbols in this file: {shown}{more}.")
    if len(matches) > 1:
        lines = ", ".join(str(m.lineno) for m in matches)  # type: ignore[attr-defined]
        raise _EditError(
            f"the symbol '{symbol}' is defined {len(matches)} times (lines {lines}), so it is "
            "ambiguous. Use search_replace with enough surrounding lines for this one."
        )
    return matches[0], text.split("\n")


def _new_definition(code: str, expect_name: str | None = None) -> str:
    """Dedent new code and check that it parses (and, for a replacement, keeps the name)."""
    dedented = "\n".join(_trim_blank_edges(dedent_code(code.rstrip("\n")).split("\n")))
    try:
        tree = ast.parse(dedented)
    except SyntaxError as exc:
        raise _EditError(
            f"the new code does not parse on its own (line {exc.lineno}: {exc.msg}). "
            "Send complete, correctly indented code."
        ) from None
    if expect_name is not None:
        defined = [n.name for n in tree.body if isinstance(n, _DEF_TYPES)]
        if expect_name not in defined:
            found = ", ".join(defined) if defined else "no function or class"
            raise _EditError(
                f"the new code defines {found}, but the symbol being replaced is "
                f"'{expect_name}'. A replacement must keep the name. To add a new function or "
                "class, use insert_after_symbol instead."
            )
    return dedented


# --------------------------------------------------------------------------- the five ops


def _replace_symbol(rel: str, file: _File, symbol: str, code: str) -> str:
    node, lines = _locate(rel, file.text, symbol)
    new_code = _new_definition(code, symbol.split(".")[-1])
    first, last = _node_span(node)
    indent = lines[node.lineno - 1][: node.col_offset]  # type: ignore[attr-defined]
    new_lines = indent_code(new_code, indent)
    result = "\n".join(lines[:first] + new_lines + lines[last + 1 :])
    _check_parses(rel, result)
    return result


def _insert_after_symbol(rel: str, file: _File, symbol: str, code: str) -> str:
    node, lines = _locate(rel, file.text, symbol)
    new_code = _new_definition(code)
    _, last = _node_span(node)
    indent = lines[node.lineno - 1][: node.col_offset]  # type: ignore[attr-defined]
    new_lines = indent_code(new_code, indent)
    top_level = node.col_offset == 0  # type: ignore[attr-defined]
    gap = [""] * (2 if top_level else 1)
    result = "\n".join(lines[: last + 1] + gap + new_lines + lines[last + 1 :])
    if not result.endswith("\n"):
        result += "\n"
    _check_parses(rel, result)
    return result


def _check_parses(rel: str, text: str) -> None:
    try:
        ast.parse(text)
    except SyntaxError as exc:
        raise _EditError(
            f"after this edit {rel} would have a syntax error on line {exc.lineno} ({exc.msg}). "
            "Check the indentation and the brackets of the code you sent."
        ) from None


def _normalize_import(node: ast.stmt) -> str:
    return ast.unparse(node)


def _add_import(rel: str, file: _File, code: str) -> str:
    if not rel.endswith(".py"):
        raise _EditError("add_import only works on Python (.py) files.")
    try:
        wanted = ast.parse(code)
    except SyntaxError as exc:
        raise _EditError(
            f"the import lines do not parse (line {exc.lineno}: {exc.msg}). "
            "Send only lines like `import os` or `from x import y`."
        ) from None
    bad = [n for n in wanted.body if not isinstance(n, ast.Import | ast.ImportFrom)]
    if bad or not wanted.body:
        raise _EditError(
            "add_import accepts only `import ...` and `from ... import ...` lines. "
            "For other code use insert_after_symbol or search_replace."
        )
    tree = _parse(rel, file.text)
    lines = file.text.split("\n")
    existing_nodes = [n for n in tree.body if isinstance(n, ast.Import | ast.ImportFrom)]
    present = {_normalize_import(n) for n in existing_nodes}
    to_add = [n for n in wanted.body if _normalize_import(n) not in present]
    if not to_add:
        return file.text
    new_lines: list[str] = []
    for n in to_add:
        text = ast.get_source_segment(code, n) or ast.unparse(n)
        new_lines.extend(text.split("\n"))
        present.add(_normalize_import(n))
    if existing_nodes:
        at = max(n.end_lineno for n in existing_nodes)  # insert after the last import
        result = lines[:at] + new_lines + lines[at:]
    else:
        at = _after_docstring_and_header(tree, lines)
        before = [""] if at > 0 and lines[at - 1].strip() else []
        after = [""] if at < len(lines) and lines[at].strip() else []
        result = lines[:at] + before + new_lines + after + lines[at:]
    return "\n".join(result)


def _after_docstring_and_header(tree: ast.Module, lines: list[str]) -> int:
    """Line index just after the shebang/encoding comments and the module docstring."""
    at = 0
    while at < len(lines) and lines[at].startswith("#"):
        at += 1
    first = tree.body[0] if tree.body else None
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        at = max(at, first.end_lineno)
    return at


def _search_replace(rel: str, file: _File, search: str | None, code: str) -> str:
    if not search or not search.strip():
        raise _EditError("the SEARCH text is empty. Copy the exact lines you want to change.")
    text = file.text
    count = text.count(search)
    if count == 1:
        return text.replace(search, code, 1)
    if count > 1:
        raise _EditError(_many_matches(text, search, count))
    tolerant = _tolerant_replace(text, search, code)
    if tolerant is not None:
        return tolerant
    raise _EditError(_not_found(text, search))


def _line_numbers(text: str, search: str) -> str:
    numbers = []
    start = 0
    while (pos := text.find(search, start)) != -1 and len(numbers) < 6:
        numbers.append(str(text.count("\n", 0, pos) + 1))
        start = pos + max(len(search), 1)
    return ", ".join(numbers)


def _many_matches(text: str, search: str, count: int) -> str:
    return (
        f"SEARCH text matches {count} places (lines {_line_numbers(text, search)}). "
        "Add more surrounding lines so it matches exactly once, or use replace_symbol."
    )


def _not_found(text: str, search: str) -> str:
    wanted = next((ln.strip() for ln in search.split("\n") if ln.strip()), "")
    lines = text.split("\n")
    scored: dict[str, float] = {}
    for line in lines:
        key = line.strip()
        if key and key not in scored:
            scored[key] = difflib.SequenceMatcher(None, wanted, key).ratio()
    ranked = sorted(scored.items(), key=lambda item: item[1], reverse=True)
    picked = [k for i, (k, score) in enumerate(ranked[:3]) if score >= (0.3 if i == 0 else 0.6)]
    quoted = []
    for match in picked:
        original = next(ln.rstrip() for ln in lines if ln.strip() == match)
        quoted.append(f'"{original}"')
    if len(quoted) == 1:
        hint = f"Closest line: {quoted[0]}. "
    elif quoted:
        hint = "Closest lines: " + " | ".join(quoted) + ". "
    else:
        hint = ""
    return f"SEARCH text not found.\n{hint}Copy the current code exactly, or use replace_symbol."


def _norm_block(lines: list[str]) -> list[str]:
    margin = _common_prefix([_leading_ws(ln) for ln in lines if ln.strip()])
    return [ln[len(margin) :].rstrip() if ln.strip() else "" for ln in lines]


def _tolerant_replace(text: str, search: str, replacement: str) -> str | None:
    """Match ignoring trailing spaces and a different base indentation, then re-indent."""
    file_lines = text.split("\n")
    s_lines = search.split("\n")
    while s_lines and not s_lines[-1].strip():
        s_lines.pop()
    while s_lines and not s_lines[0].strip():
        s_lines.pop(0)
    if not s_lines:
        return None
    s_margin = _common_prefix([_leading_ws(ln) for ln in s_lines if ln.strip()])
    s_norm = _norm_block(s_lines)
    size = len(s_lines)
    hits: list[tuple[int, str]] = []
    for start in range(len(file_lines) - size + 1):
        window = file_lines[start : start + size]
        if _norm_block(window) == s_norm:
            f_margin = _common_prefix([_leading_ws(ln) for ln in window if ln.strip()])
            hits.append((start, f_margin))
    if len(hits) != 1:
        if len(hits) > 1:
            where = ", ".join(str(s + 1) for s, _ in hits[:6])
            raise _EditError(
                f"SEARCH text matches {len(hits)} places once spacing is ignored (lines {where}). "
                "Add more surrounding lines so it matches exactly once, or use replace_symbol."
            )
        return None
    start, f_margin = hits[0]
    new_lines = [
        (f_margin + (ln[len(s_margin) :] if ln.startswith(s_margin) else ln.lstrip(" \t")))
        if ln.strip()
        else ""
        for ln in replacement.split("\n")
    ]
    if not replacement:
        new_lines = []
    return "\n".join(file_lines[:start] + new_lines + file_lines[start + size :])


def _create_file(rel: str, code: str) -> str:
    text = code if code.endswith("\n") else code + "\n"
    if rel.endswith(".py"):
        try:
            ast.parse(text)
        except SyntaxError as exc:
            raise _EditError(
                f"the new file does not parse (line {exc.lineno}: {exc.msg}). "
                "Send complete, correctly indented code."
            ) from None
    return text


# --------------------------------------------------------------------------- policy


def _policy_error(
    op: str, rel: str, role: str, protected: list[str], allow_protected: bool
) -> str | None:
    if role == "test_writer":
        if op != "create_file":
            return "the test writer may only create new test files, not change existing files."
        if not is_test_file(rel):
            return f"the test writer may only create test files (tests/..., test_*.py), not {rel}."
        return None
    if is_protected(rel, protected) and not allow_protected:
        return (
            f"{rel} is a protected file (existing tests and test configuration cannot be "
            "changed). Change the code under test instead."
        )
    return None


def _label(n: int, edit: EditOp) -> str:
    symbol = f" symbol={edit.symbol}" if edit.symbol else ""
    return f"EDIT {n} ({edit.op} path={edit.path}{symbol})"


def apply_edits_report(
    worktree: Path | str,
    edits: list[EditOp],
    role: str,
    *,
    protected: list[str] | None = None,
    allow_protected: bool = False,
) -> ApplyReport:
    """Apply `edits` in order inside `worktree`; write only if every edit succeeds."""
    root = Path(worktree)
    patterns = DEFAULT_PROTECTED if protected is None else protected
    files = _Files(root)
    report = ApplyReport()
    if not edits:
        report.errors.append("There are no edits to apply.")
        return report
    for number, edit in enumerate(edits, start=1):
        label = _label(number, edit)
        try:
            _apply_one(files, root, edit, role, patterns, allow_protected)
        except _EditError as exc:
            report.errors.append(f"{label}: {exc}")
    if report.errors:
        return report
    try:
        report.changed, report.created = files.write_all()
    except OSError as exc:
        report.errors.append(
            f"Could not write the changed files ({exc}). Nothing else was done; try again."
        )
    return report


def _apply_js(edit: EditOp, rel: str, current: _File) -> str:
    """The three symbol operations for JavaScript and TypeScript files (tree-sitter)."""
    try:
        if edit.op == "replace_symbol":
            return js_symbols.replace_symbol(rel, current.text, edit.symbol or "", edit.code)
        if edit.op == "insert_after_symbol":
            return js_symbols.insert_after_symbol(rel, current.text, edit.symbol or "", edit.code)
        return js_symbols.add_import(rel, current.text, edit.code)
    except js_symbols.JsEditError as exc:
        raise _EditError(str(exc)) from None


def _apply_one(
    files: _Files,
    root: Path,
    edit: EditOp,
    role: str,
    protected: list[str],
    allow_protected: bool,
) -> None:
    problem = validate_path(edit.path, root)
    if problem:
        raise _EditError(f"bad path: {problem}.")
    rel = normalize_path(edit.path)
    policy = _policy_error(edit.op, rel, role, protected, allow_protected)
    if policy:
        raise _EditError(policy)
    current = files.get(rel)
    if edit.op == "create_file":
        if current is not None:
            raise _EditError(
                f"{rel} already exists. To change it use replace_symbol or search_replace; "
                "create_file is only for new files."
            )
        files.put(rel, _File(_create_file(rel, edit.code), exists_on_disk=False))
        return
    if current is None:
        raise _EditError(
            f"{rel} does not exist. Check the path, or use create_file to make a new file."
        )
    symbol_op = edit.op in ("replace_symbol", "insert_after_symbol", "add_import")
    if symbol_op and not rel.endswith(".py") and not js_symbols.is_js_file(rel):
        raise _EditError(
            f"{edit.op} only works on Python, JavaScript and TypeScript files; "
            f"use search_replace for {rel}."
        )
    if symbol_op and js_symbols.is_js_file(rel):
        new_text = _apply_js(edit, rel, current)
    elif edit.op == "replace_symbol":
        new_text = _replace_symbol(rel, current, edit.symbol or "", edit.code)
    elif edit.op == "insert_after_symbol":
        new_text = _insert_after_symbol(rel, current, edit.symbol or "", edit.code)
    elif edit.op == "add_import":
        new_text = _add_import(rel, current, edit.code)
    else:
        new_text = _search_replace(rel, current, edit.search, edit.code)
    if new_text != current.text:
        files.put(
            rel,
            _File(new_text, current.crlf, current.bom, exists_on_disk=current.exists_on_disk),
        )


def apply_edits(
    worktree: Path | str,
    edits: list[EditOp],
    role: str,
    *,
    protected: list[str] | None = None,
    allow_protected: bool = False,
) -> list[str]:
    """Apply a reply's edits to a worktree. Returns the errors; an empty list means success."""
    return apply_edits_report(
        worktree, edits, role, protected=protected, allow_protected=allow_protected
    ).errors
