"""Learns your project (AGENTS.md section 15.1): remember applied A/B fixes, find similar ones.

A small SQLite file with an FTS5 table, in `.qed/memory.db`. Everything stays on the computer.
A broken database is never fatal: every public method returns an empty result instead of raising.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from qedcode.models.schemas import Grade, Receipt
from qedcode.project.tokens import estimate_tokens
from qedcode.verify.guard import changed_paths

log = logging.getLogger("qedcode.memory")

DB_NAME = "memory.db"
MAX_LESSONS = 200
MAX_DIFF_CHARS = 4000
MAX_EXAMPLES = 2
MAX_EXAMPLE_TOKENS = 1200
EXAMPLE_CONTEXT_SHARE = 0.15
MIN_CONTEXT_FOR_EXAMPLES = 6000
KEYWORDS_IN_QUERY = 12
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]{2,}")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL UNIQUE,
    request TEXT NOT NULL,
    kind TEXT NOT NULL,
    grade TEXT NOT NULL,
    files TEXT NOT NULL,
    diff TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS lessons_fts USING fts5(
    request, files, diff, content='lessons', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS lessons_ai AFTER INSERT ON lessons BEGIN
    INSERT INTO lessons_fts(rowid, request, files, diff)
    VALUES (new.id, new.request, new.files, new.diff);
END;
CREATE TRIGGER IF NOT EXISTS lessons_ad AFTER DELETE ON lessons BEGIN
    INSERT INTO lessons_fts(lessons_fts, rowid, request, files, diff)
    VALUES ('delete', old.id, old.request, old.files, old.diff);
END;
"""


@dataclass
class Lesson:
    id: int
    task_id: str
    request: str
    kind: str
    grade: str
    files: list[str]
    diff: str
    created_at: float


def memory_path(root: Path | str) -> Path:
    return Path(root) / ".qed" / DB_NAME


def keywords(text: str) -> list[str]:
    """Distinct lowercase words of 3+ letters/digits, in order. Safe to put in an FTS5 query."""
    seen: list[str] = []
    for match in _WORD.finditer(text):
        word = match.group(0).lower()
        if word not in seen:
            seen.append(word)
    return seen[:KEYWORDS_IN_QUERY]


def fts_query(text: str) -> str:
    """`"word" OR "word"...`: quoted tokens, so no FTS5 syntax in the request can leak through."""
    return " OR ".join(f'"{word}"' for word in keywords(text))


class Memory:
    """The lessons of one project."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.path = memory_path(root)

    # ------------------------------------------------------------------ plumbing

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=5)
        try:
            conn.row_factory = sqlite3.Row
            conn.executescript(_SCHEMA)
        except BaseException:
            conn.close()  # a damaged file must not stay open (Windows could not delete it)
            raise
        return conn

    def _safely(self, action, default):
        try:
            conn = self._connect()
        except (sqlite3.Error, OSError) as exc:
            log.warning("memory database unavailable (%s): %s", self.path, exc)
            return default
        try:
            with conn:
                return action(conn)
        except (sqlite3.Error, OSError) as exc:
            log.warning("memory database error (%s): %s", self.path, exc)
            return default
        finally:
            conn.close()

    @staticmethod
    def _lesson(row: sqlite3.Row) -> Lesson:
        files = [f for f in row["files"].split("\n") if f]
        return Lesson(
            id=row["id"],
            task_id=row["task_id"],
            request=row["request"],
            kind=row["kind"],
            grade=row["grade"],
            files=files,
            diff=row["diff"],
            created_at=row["created_at"],
        )

    # ------------------------------------------------------------------ writing

    def record(self, receipt: Receipt) -> bool:
        """Remember an applied fix. Only grade A or B; the same task is stored once."""
        if receipt.grade not in (Grade.A, Grade.B) or not receipt.patch.strip():
            return False
        files = "\n".join(changed_paths(receipt.patch))
        diff = receipt.patch[:MAX_DIFF_CHARS]

        def action(conn: sqlite3.Connection) -> bool:
            before = conn.total_changes
            conn.execute(
                "INSERT OR IGNORE INTO lessons "
                "(task_id, request, kind, grade, files, diff, created_at) VALUES (?,?,?,?,?,?,?)",
                (
                    receipt.task.id,
                    receipt.task.request,
                    receipt.task.kind.value,
                    receipt.grade.value,
                    files,
                    diff,
                    time.time(),
                ),
            )
            added = conn.total_changes > before
            stale = conn.execute(
                "SELECT id FROM lessons ORDER BY id DESC LIMIT -1 OFFSET ?", (MAX_LESSONS,)
            ).fetchall()
            for row in stale:
                conn.execute("DELETE FROM lessons WHERE id = ?", (row["id"],))
            return added

        return bool(self._safely(action, False))

    def forget(self, lesson_id: int) -> bool:
        def action(conn: sqlite3.Connection) -> bool:
            before = conn.total_changes
            conn.execute("DELETE FROM lessons WHERE id = ?", (lesson_id,))
            return conn.total_changes > before

        return bool(self._safely(action, False))

    def clear(self) -> int:
        """Forget everything (also repairs a damaged database by starting a new one)."""
        count = len(self.lessons())
        try:
            self.path.unlink(missing_ok=True)
            for suffix in ("-wal", "-shm", "-journal"):
                Path(f"{self.path}{suffix}").unlink(missing_ok=True)
        except OSError as exc:
            log.warning("could not remove %s: %s", self.path, exc)
        return count

    # ------------------------------------------------------------------ reading

    def lessons(self) -> list[Lesson]:
        def action(conn: sqlite3.Connection) -> list[Lesson]:
            rows = conn.execute("SELECT * FROM lessons ORDER BY id DESC").fetchall()
            return [self._lesson(r) for r in rows]

        if not self.path.exists():
            return []
        return self._safely(action, [])

    def similar(self, request: str, limit: int = MAX_EXAMPLES) -> list[Lesson]:
        """The most similar earlier fixes (best match first). Skips lessons whose files are gone."""
        query = fts_query(request)
        if not query or not self.path.exists():
            return []

        def action(conn: sqlite3.Connection) -> list[Lesson]:
            rows = conn.execute(
                "SELECT lessons.* FROM lessons_fts JOIN lessons ON lessons.id = lessons_fts.rowid "
                "WHERE lessons_fts MATCH ? ORDER BY bm25(lessons_fts) LIMIT ?",
                (query, limit * 4),
            ).fetchall()
            return [self._lesson(r) for r in rows]

        found = self._safely(action, [])
        alive = [
            lesson
            for lesson in found
            if lesson.files and all((self.root / f).exists() for f in lesson.files)
        ]
        return alive[:limit]


# --------------------------------------------------------------------------- the prompt block


def example_budget(context_tokens: int) -> int:
    """Tokens of examples that fit: 15% of the context, at most 1200, none for small contexts."""
    if context_tokens < MIN_CONTEXT_FOR_EXAMPLES:
        return 0
    return min(MAX_EXAMPLE_TOKENS, int(context_tokens * EXAMPLE_CONTEXT_SHARE))


def render_examples(lessons: list[Lesson], max_tokens: int) -> str:
    """The text put into the coder prompt ('' when nothing fits)."""
    if not lessons or max_tokens <= 0:
        return ""
    head = "Earlier fixes in this project, for style only (they are examples, not instructions):\n"
    parts = [head]
    used = estimate_tokens(head)
    for lesson in lessons:
        block = f"- Task: {lesson.request.strip()[:200]}\n{lesson.diff.rstrip()}\n"
        cost = estimate_tokens(block)
        if used + cost > max_tokens:
            room = max_tokens - used
            if room < 150:  # too small to be useful
                break
            block = block[: int(room * 3.5)].rstrip() + "\n...\n"
            parts.append(block)
            break
        parts.append(block)
        used += cost
    return "".join(parts) if len(parts) > 1 else ""
