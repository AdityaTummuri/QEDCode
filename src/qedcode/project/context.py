"""Context for prompts and checks (AGENTS.md sections 8 and 9).

This part: choosing which tests to run for a change. Selection rules, in order:
  1. the repro test file, if any;
  2. test files named after a changed module (test_<stem>.py or <stem>_test.py);
  3. test files that import a changed module;
  4. if nothing matched and the baseline full suite took 60 seconds or less, the full suite.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from qedcode.models.schemas import Event, TaskSpec
from qedcode.project.detect import find_test_files
from qedcode.project.detect_js import resolve_js_import
from qedcode.project.paths import is_js_path, is_test_file, normalize_path, validate_path
from qedcode.project.repo_map import MAX_FILE_BYTES, SKIP_DIRS, list_source_files, repo_map
from qedcode.project.search import extract_keywords, format_hits, search
from qedcode.project.tokens import CHARS_PER_TOKEN, estimate_tokens, prompt_budget

FULL_SUITE_FAST_SECONDS = 60.0
_SOURCE_ROOTS = ("src", "lib", "python")
JS_IMPORT = re.compile(
    r"""(?:from\s+|import\s*\(\s*|import\s+|require\s*\(\s*)["'](\.{1,2}/[^"']*|\.{1,2})["']"""
)


def module_names(rel_path: str, repo: Path) -> list[str]:
    """Dotted module names a changed .py file may be imported as (several guesses, best first)."""
    path = Path(rel_path)
    if path.suffix != ".py":
        return []
    parts = list(path.with_suffix("").parts)
    is_init = parts[-1] == "__init__"
    if is_init:
        parts = parts[:-1]
    if not parts:
        return []
    # walk up while the folders are packages (have __init__.py): src/shop/pages.py -> shop.pages
    index = len(parts) - (0 if is_init else 1)
    while index > 0 and (repo / Path(*parts[:index]) / "__init__.py").exists():
        index -= 1
    names = [".".join(parts[index:]), ".".join(parts)]
    if parts[0] in _SOURCE_ROOTS and len(parts) > 1:
        names.append(".".join(parts[1:]))
    unique: list[str] = []
    for name in names:
        if name and name not in unique:
            unique.append(name)
    return unique


def imports_module(text: str, dotted_names: list[str]) -> bool:
    """Does Python source `text` import one of the dotted module names?

    Matches `import pkg.mod`, `from pkg.mod import x`, `from pkg import mod`, and a bare
    `import pkg` when the dotted name is then used (pkg.mod.attr).
    """
    for dotted in dotted_names:
        pattern = rf"(?m)^[ \t]*(?:from|import)[ \t]+{re.escape(dotted)}\b"
        if re.search(pattern, text):
            return True
        package, _, leaf = dotted.rpartition(".")
        if not package:
            continue
        from_import = (
            rf"(?m)^[ \t]*from[ \t]+{re.escape(package)}[ \t]+import[ \t]+"
            rf"[^\n#]*\b{re.escape(leaf)}\b"
        )
        if re.search(from_import, text):
            return True
        bare = rf"(?m)^[ \t]*import[ \t]+{re.escape(package)}[ \t]*(?:#.*)?$"
        if re.search(bare, text) and re.search(rf"\b{re.escape(dotted)}\b", text):
            return True
    return False


def related_js_tests(changed_paths: list[str], repo: Path) -> list[str]:
    """JS/TS test files related to the changed files by name (foo.test.ts) or by relative import."""
    tests = find_test_files(repo, "javascript")
    sources = [p for p in changed_paths if is_js_path(p) and not is_test_file(p)]
    chosen = {p for p in changed_paths if is_test_file(p) and p in tests}
    if not sources:
        return sorted(chosen)
    stems = {Path(p).stem for p in sources if Path(p).stem != "index"}
    for test in tests:
        name = Path(test).name
        base = (
            name.split(".test.")[0].split(".spec.")[0]
            if (".test." in name or ".spec." in name)
            else ""
        )
        if base and base in stems:
            chosen.add(test)
    for test in tests:
        if test in chosen:
            continue
        try:
            text = (repo / test).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(resolve_js_import(test, spec, set(sources)) for spec in JS_IMPORT.findall(text)):
            chosen.add(test)
    return sorted(chosen)


def related_tests(
    changed_paths: list[str], repo: Path | str, language: str = "python"
) -> list[str]:
    """Test files (repo-relative, sorted) related to the changed files by name or by import."""
    repo = Path(repo)
    if language == "javascript":
        return related_js_tests(changed_paths, repo)
    tests = find_test_files(repo)
    if not tests:
        return []
    chosen: set[str] = set()
    sources = [p for p in changed_paths if p.endswith(".py")]
    for changed in sources:
        if is_test_file(changed) and changed in tests:
            chosen.add(changed)
    stems = {Path(p).stem for p in sources if not is_test_file(p) and Path(p).stem != "__init__"}
    for test in tests:
        name = Path(test).stem
        if any(name == f"test_{stem}" or name == f"{stem}_test" for stem in stems):
            chosen.add(test)
    dotted = [n for p in sources if not is_test_file(p) for n in module_names(p, repo)]
    if dotted:
        for test in tests:
            if test in chosen:
                continue
            try:
                text = (repo / test).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if imports_module(text, dotted):
                chosen.add(test)
    return sorted(chosen)


def select_targeted(
    changed_paths: list[str],
    repo: Path | str,
    *,
    repro_files: list[str] | None = None,
    baseline_full_seconds: float | None = None,
    language: str = "python",
) -> list[str] | None:
    """The tests to run for a candidate: repro first, then related files.

    Returns None to mean "run the full suite": nothing matched and the baseline full suite was
    quick (<= 60 s). Returns [] when nothing matched and the suite is slow or unknown.
    """
    repro = list(repro_files or [])
    related = [t for t in related_tests(changed_paths, repo, language) if t not in repro]
    selection = [*repro, *related]
    if not related and not repro:
        if baseline_full_seconds is not None and baseline_full_seconds <= FULL_SUITE_FAST_SECONDS:
            return None
        return []
    return selection


# --------------------------------------------------------------------------- prompt budget
# Prompt budget = model context - max output tokens - 512. Tokens are estimated as characters / 3.5.
# Over budget: drop whole files from the end of the locator's list first, then replace a large file
# with only its relevant symbols plus an outline of the rest.


@dataclass
class ContextFile:
    """A file shown to the model; `condensed` means only relevant symbols plus an outline."""

    path: str
    content: str
    condensed: bool = False


def files_block(files: list[ContextFile]) -> str:
    """`=== path ===` followed by the content, for each file, without line numbers."""
    return "\n".join(f"=== {f.path} ===\n{f.content.rstrip(chr(10))}\n" for f in files).rstrip("\n")


def _header(lines: list[str], node: ast.AST) -> str:
    """The signature lines of a def/class (up to its first body statement)."""
    start = node.lineno - 1  # type: ignore[attr-defined]
    body = getattr(node, "body", [])
    end = (body[0].lineno - 1) if body else start + 1
    first = body[0] if body else None
    if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant):
        end = start + 1
        while end < len(lines) and not lines[end - 1].rstrip().endswith(":"):
            end += 1
    return "\n".join(lines[start:end]).rstrip()


def condense_file(content: str, keywords: set[str]) -> str:
    """Keep the module header and the relevant symbols in full; outline everything else."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return content
    lines = content.split("\n")
    wanted = {k.lower() for k in keywords if len(k) >= 3}

    def relevant(name: str) -> bool:
        low = name.lower()
        return any(k == low or (len(k) >= 4 and k in low) for k in wanted)

    out = ["# (outline view: bodies of unrelated functions were left out to fit the context)"]
    for node in tree.body:
        text = "\n".join(lines[node.lineno - 1 : node.end_lineno])
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            deco = [lines[d.lineno - 1] for d in node.decorator_list]
            if relevant(node.name):
                start = min([node.lineno, *[d.lineno for d in node.decorator_list]]) - 1
                out.append("\n".join(lines[start : node.end_lineno]))
            elif isinstance(node, ast.ClassDef):
                members = [
                    _header(lines, m) + "\n        ..."
                    for m in node.body
                    if isinstance(m, ast.FunctionDef | ast.AsyncFunctionDef)
                ]
                out.append(
                    "\n".join([*deco, _header(lines, node), *members]) or _header(lines, node)
                )
            else:
                out.append("\n".join([*deco, _header(lines, node), "    ..."]))
        else:
            out.append(text)
    return "\n".join(out) + "\n"


def fit_files(
    files: list[ContextFile], budget_tokens: int, keywords: set[str] | None = None
) -> list[ContextFile]:
    """Make the files fit `budget_tokens`: drop trailing files, then condense, then truncate."""
    keywords = keywords or set()
    chosen = list(files)

    def total(items: list[ContextFile]) -> int:
        return estimate_tokens(files_block(items))

    while len(chosen) > 1 and total(chosen) > budget_tokens:
        chosen.pop()
    if total(chosen) <= budget_tokens:
        return chosen
    chosen = [ContextFile(f.path, condense_file(f.content, keywords), True) for f in chosen]
    if total(chosen) <= budget_tokens:
        return chosen
    limit = max(int(budget_tokens * CHARS_PER_TOKEN) - 150 * len(chosen), 200) // max(
        len(chosen), 1
    )
    return [
        ContextFile(f.path, f.content[:limit].rsplit("\n", 1)[0] + "\n# (shortened to fit)\n", True)
        for f in chosen
    ]


def task_keywords(text: str) -> set[str]:
    """Identifier-like words from the task text (used to pick the relevant symbols)."""
    return {w for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text)}


def repro_test_path(
    request: str, existing_tests: list[str], repo: Path | str, language: str = "python"
) -> str:
    """Where the repro test goes: tests/test_qed_<slug>.py, or next to the existing tests.

    JavaScript and TypeScript projects get `qed_<slug>.test.js` (`.test.ts` for TypeScript)."""
    words = re.findall(r"[a-z0-9]+", request.lower())[:5]
    slug = "_".join(words) or "task"
    folder = "tests"
    if existing_tests:
        parents = [str(Path(t).parent.as_posix()) for t in existing_tests]
        folder = max(set(parents), key=parents.count)
        folder = "" if folder == "." else folder
    repo = Path(repo)
    if language == "javascript":
        ext = js_test_extension(existing_tests, repo)
        base = f"qed_{slug}"
        for suffix in ("", "_2", "_3", "_4"):
            rel = f"{folder}/{base}{suffix}.test.{ext}" if folder else f"{base}{suffix}.test.{ext}"
            if not (repo / rel).exists():
                return rel
        return f"{folder}/{base}_x.test.{ext}" if folder else f"{base}_x.test.{ext}"
    base = f"test_qed_{slug}"
    for suffix in ("", "_2", "_3", "_4"):
        rel = f"{folder}/{base}{suffix}.py" if folder else f"{base}{suffix}.py"
        if not (repo / rel).exists():
            return rel
    return f"{folder}/{base}_x.py" if folder else f"{base}_x.py"


def js_test_extension(existing_tests: list[str], repo: Path) -> str:
    """'ts' for a TypeScript project, else the extension most existing tests use ('js')."""
    suffixes = [Path(t).suffix.lstrip(".") for t in existing_tests if is_js_path(t)]
    if suffixes:
        common = max(set(suffixes), key=suffixes.count)
        return {"tsx": "ts", "jsx": "js", "cjs": "cjs", "mjs": "mjs"}.get(common, common)
    return "ts" if (repo / "tsconfig.json").is_file() else "js"


# --------------------------------------------------------------------------- building the context


LOCATOR_MAX_TOKENS = 200
CODER_FILE_BUDGET_MARGIN = 1800  # the fixed parts of the coder prompt (instructions, cheat sheet)
STRONG_HIT_SCORE = 3.0
COMPACT_BUDGET_TOKENS = 1500


@dataclass
class ContextBundle:
    """What the controller shows the model: files, the repo map and search hits."""

    files: list[ContextFile]
    repo_map: str = ""
    hits: str = ""
    located: list[str] = field(default_factory=list)
    related_tests: list[str] = field(default_factory=list)
    example_test: ContextFile | None = None
    keywords: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)

    def compact(self, budget_tokens: int = COMPACT_BUDGET_TOKENS) -> ContextBundle:
        """A much smaller context for a helper model: the first files, condensed, no map."""
        files = fit_files(self.files[:3], budget_tokens, self.keywords)
        return ContextBundle(
            files=files,
            located=[f.path for f in files],
            related_tests=self.related_tests,
            example_test=self.example_test,
            keywords=self.keywords,
            notes=["compact context"],
        )


def read_project_file(root: Path | str, rel: str) -> str | None:
    """Text of a project file, or None when it is missing, too large or not readable text."""
    path = Path(root) / rel
    try:
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            return None
        return path.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return None


def validate_located(root: Path | str, paths: list[str]) -> list[str]:
    """Keep the locator's paths that are real project files (inside the repo, not env folders)."""
    root = Path(root)
    kept: list[str] = []
    for raw in paths:
        rel = normalize_path(raw)
        if validate_path(rel, root) or set(rel.split("/")[:-1]) & SKIP_DIRS:
            continue
        if rel not in kept and read_project_file(root, rel) is not None:
            kept.append(rel)
    return kept


def build_context(
    root: Path | str,
    spec: TaskSpec,
    locate: Callable[[str, str], list[str]],
    *,
    max_files: int,
    context_tokens: int,
    on_event: Callable[[Event], None] | None = None,
    language: str = "python",
) -> ContextBundle:
    """Find the files for a task: search, repo map, the locator, validation and fallbacks.

    `locate(repo_map, hits)` is the locator role. Its paths are validated; when it gives nothing
    usable the best search hits are used instead; when it picked fewer than `max_files`, a strong
    search match it missed is added.
    """
    root = Path(root)
    notes: list[str] = []
    py_files = list_source_files(root, language)
    keywords = extract_keywords(spec.request, spec.acceptance)
    found = search(root, keywords, files=py_files)
    hits_text = format_hits(found, 20)
    budget = prompt_budget(context_tokens, LOCATOR_MAX_TOKENS)
    fixed = estimate_tokens(spec.request + "\n".join(spec.acceptance)) + 400
    map_budget = max(200, budget - fixed - estimate_tokens(hits_text))
    map_text = repo_map(root, map_budget, py_files, language)
    located = validate_located(root, locate(map_text, hits_text))
    top_sources = found.top_source_files(1)
    top = top_sources[0] if top_sources else None
    if not located:
        located = validate_located(root, found.top_source_files(max_files))
        if located:
            notes.append("The locator gave no usable files, so the best search matches were used.")
    elif (
        top
        and top not in located
        and len(located) < max_files + 1
        and (
            found.scores.get(top, 0.0) >= STRONG_HIT_SCORE
            or all(is_test_file(p) for p in located)  # only tests were picked: add real code
        )
    ):
        located = located[: max_files - 1] + [top] if len(located) >= max_files else [*located, top]
        notes.append(f"Added {top}, the best search match the locator did not pick.")
    if not located:
        source = [p for p in py_files if not is_test_file(p)]
        located = validate_located(root, source[:max_files])
        notes.append("No file stood out, so the first source files are shown.")
    located = located[:max_files]
    related = related_tests(located, root, language)
    tests = find_test_files(root, language)
    example_path = related[0] if related else (tests[0] if tests else None)
    example = None
    if example_path:
        text = read_project_file(root, example_path)
        example = ContextFile(example_path, text) if text is not None else None
    raw_files = [
        ContextFile(rel, text)
        for rel in located
        if (text := read_project_file(root, rel)) is not None
    ]
    files = fit_files(
        raw_files,
        max(256, prompt_budget(context_tokens, 2000) - CODER_FILE_BUDGET_MARGIN),
        task_keywords(spec.request),
    )
    if on_event is not None:
        on_event(
            Event(
                kind="stage",
                message="Finding files ...... " + ", ".join(f.path for f in files),
                data={"files": [f.path for f in files]},
            )
        )
    return ContextBundle(
        files=files,
        repo_map=map_text,
        hits=hits_text,
        located=[f.path for f in files],
        related_tests=related,
        example_test=example,
        keywords=task_keywords(spec.request),
        notes=notes,
    )


def files_at_commit(root: Path | str, commit: str, paths: list[str]) -> list[ContextFile]:
    """The contents of `paths` as they are in `commit` (files that do not exist are skipped)."""
    from qedcode.vcs.git import git

    out: list[ContextFile] = []
    for rel in paths:
        shown = git(root, "show", f"{commit}:{rel}", exact=True)
        if shown.ok and len(shown.stdout) <= MAX_FILE_BYTES:
            out.append(ContextFile(rel, shown.stdout))
    return out
