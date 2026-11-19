"""Keyword search over the project, driven by the task text.

Keywords come from the task: identifiers (snake_case, CamelCase, dotted names, names followed by
brackets), quoted text, error names and messages, file names and, with less weight, plain words.
Search uses ripgrep when it is installed and pure Python otherwise; both give the same hits.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from qedcode.project.paths import is_test_file
from qedcode.project.repo_map import MAX_FILE_BYTES, list_python_files
from qedcode.verify.run import run

STOPWORDS = {
    "about",
    "above",
    "after",
    "again",
    "also",
    "because",
    "before",
    "being",
    "below",
    "between",
    "both",
    "could",
    "doing",
    "during",
    "each",
    "every",
    "from",
    "have",
    "having",
    "here",
    "into",
    "just",
    "like",
    "make",
    "more",
    "most",
    "much",
    "need",
    "only",
    "other",
    "please",
    "should",
    "some",
    "such",
    "than",
    "that",
    "their",
    "them",
    "then",
    "there",
    "these",
    "they",
    "this",
    "those",
    "through",
    "under",
    "until",
    "very",
    "want",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "will",
    "with",
    "would",
    "your",
    "fix",
    "bug",
    "can",
    "you",
    "the",
    "and",
    "not",
    "but",
    "for",
    "are",
    "has",
    "had",
    "its",
    "get",
    "gets",
    "work",
    "works",
    "working",
    "does",
    "dont",
    "doesnt",
    "wrong",
    "right",
    "instead",
    "expected",
    "returns",
    "return",
    "gives",
    "give",
    "using",
    "used",
    "code",
    "function",
    "functions",
    "method",
    "class",
    "file",
    "files",
    "change",
    "changes",
    "add",
    "new",
    "one",
    "two",
    "three",
    "keeps",
    "keep",
    "never",
    "always",
    "start",
    "starts",
    "less",
    "exactly",
    "something",
    "anything",
    "everything",
}
W_FILENAME, W_QUOTED, W_IDENT, W_ERROR, W_WORD = 4.0, 3.0, 3.0, 2.0, 1.0
TEST_PENALTY = 0.6
MAX_LINES_PER_FILE = 2
LINE_LIMIT = 200


@dataclass(frozen=True)
class Keyword:
    text: str
    weight: float
    exact_case: bool = False


@dataclass
class SearchHit:
    path: str
    line: int
    text: str
    keyword: str
    weight: float


@dataclass
class SearchResult:
    keywords: list[Keyword] = field(default_factory=list)
    hits: list[SearchHit] = field(default_factory=list)  # best lines first
    ranked_files: list[str] = field(default_factory=list)  # best files first, tests after source
    scores: dict[str, float] = field(default_factory=dict)

    def top_source_files(self, n: int) -> list[str]:
        return [p for p in self.ranked_files if not is_test_file(p)][:n]


# --------------------------------------------------------------------------- keywords

_QUOTED = re.compile(r"""(?:"([^"\n]{3,80})"|'([^'\n]{3,80})'|`([^`\n]{2,80})`)""")
_DOTTED = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\b")
_IDENT = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
_FILE = re.compile(r"\b[\w./-]+\.(?:py|toml|cfg|ini|txt|md|json|ya?ml)\b")
_ERROR = re.compile(r"\b[A-Z][A-Za-z]*(?:Error|Exception|Warning)\b")


def extract_keywords(task: str, acceptance: list[str] | None = None) -> list[Keyword]:
    """Pick search keywords out of the task text, strongest first."""
    text = task + "\n" + "\n".join(acceptance or [])
    found: dict[str, Keyword] = {}

    def add(word: str, weight: float, exact: bool = False) -> None:
        key = word.lower() if not exact else word
        word = word.strip()
        if len(word) < 2 or word.isdigit() or word.lower() in STOPWORDS:
            return
        old = found.get(key)
        if old is None or old.weight < weight:
            found[key] = Keyword(word, weight, exact)

    for match in _FILE.finditer(text):
        name = match.group(0)
        add(Path(name).name, W_FILENAME)
        add(Path(name).stem, W_FILENAME)
    for match in _QUOTED.finditer(text):
        quoted = next(g for g in match.groups() if g)
        add(quoted.strip(), W_QUOTED)
    for match in _DOTTED.finditer(text):
        dotted = match.group(0)
        if not re.fullmatch(r"\d+(\.\d+)+", dotted) and not _FILE.fullmatch(dotted):
            add(dotted, W_IDENT, True)
            for part in dotted.split("."):
                add(part, W_IDENT, True)
    for match in _ERROR.finditer(text):
        add(match.group(0), W_ERROR, True)
    for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", text):  # name(...) in the text
        add(match.group(1), W_IDENT, True)
    for match in _IDENT.finditer(text):
        word = match.group(0)
        if _ERROR.fullmatch(word):
            continue  # error names keep their own (lower) weight
        if "_" in word.strip("_") or re.search(r"[a-z][A-Z]", word):
            add(word, W_IDENT, True)
        elif len(word) >= 4 and word.lower() not in STOPWORDS and not word.isdigit():
            add(word, W_WORD)
            if len(word) >= 6 and word.lower().endswith("s"):
                add(word[:-1], W_WORD)
    return sorted(found.values(), key=lambda k: -k.weight)


# --------------------------------------------------------------------------- searching


def _line_matches(line: str, kw: Keyword) -> bool:
    return kw.text in line if kw.exact_case else kw.text.lower() in line.lower()


def _python_search(root: Path, files: list[str], keywords: list[Keyword]) -> list[SearchHit]:
    hits: list[SearchHit] = []
    for rel in files:
        try:
            path = root / rel
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            lines = path.read_text(encoding="utf-8", errors="replace").split("\n")
        except OSError:
            continue
        for number, line in enumerate(lines, start=1):
            for kw in keywords:
                if _line_matches(line, kw):
                    hits.append(
                        SearchHit(rel, number, line.strip()[:LINE_LIMIT], kw.text, kw.weight)
                    )
    return hits


def _ripgrep_search(
    root: Path, files: list[str], keywords: list[Keyword], rg: str
) -> list[SearchHit] | None:
    """Candidate lines from ripgrep, then matched to keywords in Python. None if rg failed."""
    args = [rg, "--no-heading", "--line-number", "--with-filename", "-i", "-F", "--no-config"]
    for kw in keywords:
        args += ["-e", kw.text]
    args += ["--", *files]
    result = run(args, cwd=root, timeout=60)
    if result.error or result.exit_code not in (0, 1):
        return None
    hits: list[SearchHit] = []
    for raw in result.stdout.splitlines():
        parts = raw.split(":", 2)
        if len(parts) != 3 or not parts[1].isdigit():
            continue
        rel, number, line = parts[0].replace("\\", "/"), int(parts[1]), parts[2]
        for kw in keywords:
            if _line_matches(line, kw):
                hits.append(SearchHit(rel, number, line.strip()[:LINE_LIMIT], kw.text, kw.weight))
    return hits


def search(
    root: Path | str,
    keywords: list[Keyword],
    *,
    files: list[str] | None = None,
    use_ripgrep: bool = True,
) -> SearchResult:
    """Search the project's Python files for the keywords and rank the files."""
    root = Path(root)
    paths = files if files is not None else list_python_files(root)
    result = SearchResult(keywords=list(keywords))
    if not keywords or not paths:
        return result
    hits: list[SearchHit] | None = None
    rg = shutil.which("rg") if use_ripgrep else None
    if rg and len(paths) <= 3000:
        hits = _ripgrep_search(root, paths, keywords, rg)
    if hits is None:
        hits = _python_search(root, paths, keywords)
    stems = {Path(p).stem.lower(): p for p in paths}
    scores: dict[str, float] = {}
    by_file: dict[str, dict[str, int]] = {}
    for hit in hits:
        seen = by_file.setdefault(hit.path, {})
        seen[hit.keyword] = seen.get(hit.keyword, 0) + 1
    weights = {k.text: k.weight for k in keywords}
    for rel, counted in by_file.items():
        score = sum(weights[k] * (1 + 0.1 * min(n - 1, 3)) for k, n in counted.items())
        scores[rel] = score
    for kw in keywords:  # a keyword equal to a file's stem is a strong pointer to that file
        target = stems.get(kw.text.lower())
        if target and (kw.weight >= W_FILENAME or (kw.weight >= W_WORD and len(kw.text) >= 4)):
            scores[target] = scores.get(target, 0.0) + (kw.weight + 1.0)
    for rel in list(scores):
        if is_test_file(rel):
            scores[rel] *= TEST_PENALTY
    result.scores = scores
    result.ranked_files = sorted(scores, key=lambda p: (-scores[p], p))
    order = {p: i for i, p in enumerate(result.ranked_files)}
    result.hits = sorted(hits, key=lambda h: (order[h.path], -h.weight, h.line))
    return result


def format_hits(result: SearchResult, limit: int = 20) -> str:
    """`path:line: text` lines, at most two per file, best files first."""
    lines: list[str] = []
    per_file: dict[str, int] = {}
    seen: set[tuple[str, int]] = set()
    for hit in result.hits:
        if per_file.get(hit.path, 0) >= MAX_LINES_PER_FILE or (hit.path, hit.line) in seen:
            continue
        per_file[hit.path] = per_file.get(hit.path, 0) + 1
        seen.add((hit.path, hit.line))
        lines.append(f"{hit.path}:{hit.line}: {hit.text}")
        if len(lines) >= limit:
            break
    return "\n".join(lines)
