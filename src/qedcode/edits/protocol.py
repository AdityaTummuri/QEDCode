"""Parse a model's plain-text reply into edit objects (AGENTS.md section 7).

The parser is lenient (small models are messy) and the applier is strict. Accepted sloppiness is
recorded as a "warning: ..." entry in ParsedReply.errors; anything else in `errors` is a real
format problem. A reply is usable only when it has edits and no blocking errors (`reply_ok`).
"""

from __future__ import annotations

import re

from qedcode.models.schemas import EditOp, ParsedReply

OPS = ("replace_symbol", "insert_after_symbol", "add_import", "search_replace", "create_file")
SYMBOL_OPS = ("replace_symbol", "insert_after_symbol")
WARNING = "warning: "
NO_EDITS = "no EDIT blocks found"

_ATTR = re.compile(r"(\w+)=(\"[^\"]*\"|'[^']*'|`[^`]*`|\S+)")
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*)$")
_PLAN = re.compile(r"^\s*[#*`> ]*PLAN\s*:\s*\**\s*(.*)$", re.IGNORECASE)
_KEY = re.compile(r"^\s*([A-Z][A-Z _]{2,40}?)\s*:\s*(.*)$")
_FENCE_OPEN = re.compile(r"^\s*(`{3,}|~{3,})\s*([^`\s]*)[^`]*$")
_FENCE_CLOSE = re.compile(r"^\s*(`{3,}|~{3,})\s*$")
_SEARCH = re.compile(r"^\s*<{5,9}\s*SEARCH\s*$", re.IGNORECASE)
_DIVIDER = re.compile(r"^\s*={5,9}\s*$")
_REPLACE = re.compile(r"^\s*>{5,9}\s*REPLACE\s*$", re.IGNORECASE)
_COMMON_TAGS = {
    "",
    "python",
    "py",
    "python3",
    "javascript",
    "js",
    "typescript",
    "ts",
    "jsx",
    "tsx",
    "mjs",
    "cjs",
}


def reply_ok(reply: ParsedReply) -> bool:
    """True when the reply has at least one edit and no blocking format errors."""
    return bool(reply.edits) and not blocking_errors(reply)


def blocking_errors(reply: ParsedReply) -> list[str]:
    """Format problems that make the reply unusable (accepted sloppiness is not included)."""
    return [e for e in reply.errors if not e.startswith(WARNING)]


def warnings(reply: ParsedReply) -> list[str]:
    """Accepted leniencies, without the 'warning: ' prefix."""
    return [e.removeprefix(WARNING) for e in reply.errors if e.startswith(WARNING)]


def problem_text(reply: ParsedReply) -> str:
    """One sentence naming what is wrong, used when asking the model to reply again."""
    problems = blocking_errors(reply)
    if problems:
        return "; ".join(problems)
    return NO_EDITS if not reply.edits else ""


class _Parser:
    def __init__(self, text: str) -> None:
        self.crlf = "\r" in text
        self.lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        self.i = 0
        self.reply = ParsedReply()
        self.edit_count = 0
        self.prose_warned = False
        self.in_plan = False

    # ------------------------------------------------------------------ helpers

    def warn(self, message: str) -> None:
        entry = WARNING + message
        if entry not in self.reply.errors:
            self.reply.errors.append(entry)

    def error(self, message: str) -> None:
        self.reply.errors.append(message)

    def prose(self) -> None:
        if not self.prose_warned:
            self.prose_warned = True
            self.warn("ignored text outside the EDIT blocks")

    def skip_blank(self) -> None:
        while self.i < len(self.lines) and not self.lines[self.i].strip():
            self.i += 1

    # ------------------------------------------------------------------ main loop

    def parse(self) -> ParsedReply:
        if self.crlf:
            self.warn("Windows line endings were converted")
        while self.i < len(self.lines):
            line = self.lines[self.i]
            stripped = line.strip()
            if not stripped:
                self.i += 1
                continue
            header = self._header_candidate(stripped)
            if header is not None:
                self.in_plan = False
                self.i += 1
                self._edit(header, stripped)
                continue
            plan = _PLAN.match(line)
            if plan:
                self.in_plan = True
                self.i += 1
                if plan.group(1).strip():
                    self.reply.plan.append(plan.group(1).strip().lstrip("-*• ").strip())
                continue
            if self.in_plan:
                bullet = _BULLET.match(line)
                if bullet:
                    self.reply.plan.append(bullet.group(1).strip())
                    self.i += 1
                    continue
                self.in_plan = False
            key = _KEY.match(line)
            if key and key.group(1).strip() not in ("PLAN", "EDIT"):
                self.reply.extra[key.group(1).strip()] = key.group(2).strip()
                self.i += 1
                continue
            self.prose()
            self.i += 1
        if not self.reply.edits and not blocking_errors(self.reply):
            self.error(NO_EDITS)
        return self.reply

    # ------------------------------------------------------------------ edit headers

    @staticmethod
    def _header_candidate(stripped: str) -> str | None:
        """The text of an EDIT header line (markdown decoration removed), else None.

        A line counts as a header when it starts with the word EDIT and either names a known
        operation or has key=value parts; ordinary prose such as "Edit the file" does not.
        """
        core = stripped.lstrip("#*`> ")
        lead = stripped[: len(stripped) - len(core)]
        # remove a closing ** or ` only when the line also opened with one (a path may end in ")
        closers = sum(1 for c in lead if c in "*`")
        cleaned = core.rstrip()
        while closers and cleaned and cleaned[-1] in "*`":
            cleaned = cleaned[:-1]
            closers -= 1
        cleaned = cleaned.rstrip()
        match = re.match(r"(?i)^edit\s+(\w+)", cleaned)
        if match and (match.group(1).lower() in OPS or "=" in cleaned):
            return cleaned
        return None

    def _edit(self, header: str, original: str) -> None:
        self.edit_count += 1
        n = self.edit_count
        if header != original.strip():
            self.warn("markdown decoration around an EDIT header was ignored")
        if not header.startswith("EDIT "):
            self.warn("EDIT was not written in capital letters")
        match = re.match(r"(?i)^edit\s+(\w+)\s*(.*)$", header)
        op = match.group(1).lower() if match else ""
        if op not in OPS:
            self.error(
                f"EDIT {n}: unknown operation '{match.group(1) if match else header}'. "
                f"Use one of: {', '.join(OPS)}"
            )
            self._skip_body()
            return
        attrs = {k.lower(): _unquote(v) for k, v in _ATTR.findall(match.group(2))}
        raw_path = next((v for k, v in _ATTR.findall(match.group(2)) if k.lower() == "path"), "")
        if raw_path and raw_path[0] in "\"'`":
            self.warn("quotes around a path were removed")
        path = attrs.get("path", "")
        symbol = attrs.get("symbol") or None
        label = f"EDIT {n} ({op} path={path or '?'}" + (f" symbol={symbol}" if symbol else "") + ")"
        if not path:
            self.error(f"{label}: missing path=FILE")
            self._skip_body()
            return
        if symbol and "(" in symbol:
            symbol = symbol.split("(", 1)[0]
            self.warn("parentheses after a symbol name were removed")
        if op in SYMBOL_OPS and not symbol:
            self.error(f"{label}: missing symbol=NAME")
            self._skip_body()
            return
        if op == "search_replace":
            self._search_replace(path, label)
        else:
            self._fenced(op, path, symbol, label)

    def _skip_body(self) -> None:
        """After a bad header, move past its fenced body so it is not mistaken for prose."""
        self.skip_blank()
        if self.i < len(self.lines) and _FENCE_OPEN.match(self.lines[self.i]):
            self._read_fence()

    # ------------------------------------------------------------------ bodies

    def _read_fence(self) -> tuple[list[str], str, bool]:
        """Read a fenced block at self.i: (lines, language tag, closed properly)."""
        opening = _FENCE_OPEN.match(self.lines[self.i])
        assert opening is not None
        marker, tag = opening.group(1), opening.group(2).lower()
        self.i += 1
        body: list[str] = []
        while self.i < len(self.lines):
            close = _FENCE_CLOSE.match(self.lines[self.i])
            if close and close.group(1)[0] == marker[0] and len(close.group(1)) >= len(marker):
                self.i += 1
                return body, tag, True
            body.append(self.lines[self.i])
            self.i += 1
        return body, tag, False

    def _fenced(self, op: str, path: str, symbol: str | None, label: str) -> None:
        self.skip_blank()
        if self.i >= len(self.lines) or not _FENCE_OPEN.match(self.lines[self.i]):
            self.error(f"{label}: no fenced code block (```) follows the header")
            return
        body, tag, closed = self._read_fence()
        if tag not in _COMMON_TAGS:
            self.warn(f"unusual language tag '{tag}' on a code block")
        if not closed:
            self.warn("the closing ``` was missing at the end of the reply")
        while body and not body[-1].strip():
            body.pop()
        while body and not body[0].strip():
            body.pop(0)
        if not body:
            self.error(f"{label}: the code block is empty")
            return
        self.reply.edits.append(
            EditOp(
                op=op,  # type: ignore[arg-type]
                path=path,
                symbol=symbol if op in SYMBOL_OPS else None,
                code="\n".join(body) + "\n",
            )
        )

    def _search_replace(self, path: str, label: str) -> None:
        self.skip_blank()
        fenced = False
        if self.i < len(self.lines) and _FENCE_OPEN.match(self.lines[self.i]):
            fenced = True
            self.i += 1
            self.skip_blank()
        if self.i >= len(self.lines) or not _SEARCH.match(self.lines[self.i]):
            self.error(f"{label}: expected <<<<<<< SEARCH after the header")
            return
        self.i += 1
        search: list[str] = []
        while self.i < len(self.lines) and not _DIVIDER.match(self.lines[self.i]):
            search.append(self.lines[self.i])
            self.i += 1
        if self.i >= len(self.lines):
            self.error(f"{label}: missing the ======= line between the SEARCH and REPLACE text")
            return
        self.i += 1
        replace: list[str] = []
        ended = False
        while self.i < len(self.lines):
            if _REPLACE.match(self.lines[self.i]):
                ended = True
                self.i += 1
                break
            replace.append(self.lines[self.i])
            self.i += 1
        if not ended:
            self.warn("the >>>>>>> REPLACE line was missing at the end of the reply")
            while replace and (not replace[-1].strip() or _FENCE_CLOSE.match(replace[-1])):
                replace.pop()
        if fenced:
            probe = self.i
            while probe < len(self.lines) and not self.lines[probe].strip():
                probe += 1
            if probe < len(self.lines) and _FENCE_CLOSE.match(self.lines[probe]):
                self.i = probe + 1
        if not "".join(search).strip():
            self.error(f"{label}: the SEARCH text is empty")
            return
        self.reply.edits.append(
            EditOp(
                op="search_replace",
                path=path,
                search="\n".join(search),
                code="\n".join(replace),
            )
        )


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'`":
        return value[1:-1]
    return value


def parse_reply(text: str) -> ParsedReply:
    """Parse a model reply into a plan, edits, key lines and format problems. Never raises."""
    return _Parser(text or "").parse()
