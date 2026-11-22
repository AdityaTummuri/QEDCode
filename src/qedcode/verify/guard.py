"""The `scope` and `risk` stages (AGENTS.md section 8): cheap checks on the diff, before any test.

scope: protected paths, paths outside the repo, deleted files, test files created by a coder,
       weakened tests (added skip/xfail, removed asserts, asserts that can never fail) and too
       many changed lines.
risk:  a regex scan of ADDED Python lines for dangerous calls; finding one sets needs_approval.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from qedcode.config import ProjectConfig
from qedcode.engine.budget import max_changed_lines
from qedcode.models.schemas import CheckResult, Speed
from qedcode.project.paths import is_js_path, is_protected, is_test_file, validate_path

SUMMARY_LIMIT = 300
DETAILS_LIMIT = 4000
PROTECTED_MARKER = "Protected files changed:"
WEAKENED_MARKER = "Tests weakened:"


def _marked(checks: Iterable[CheckResult], marker: str) -> list[str]:
    for check in checks:
        if check.stage != "scope":
            continue
        for line in check.details.split("\n"):
            if line.startswith(marker):
                return [p.strip() for p in line[len(marker) :].split(",") if p.strip()]
    return []


def protected_from_checks(checks: Iterable[CheckResult]) -> list[str]:
    """Protected files a scope check recorded as changed (the grade function reads this)."""
    return _marked(checks, PROTECTED_MARKER)


def weakened_from_checks(checks: Iterable[CheckResult]) -> list[str]:
    """Test files a `qed check` scope stage found weakened (the grade function reads this)."""
    return _marked(checks, WEAKENED_MARKER)


# --------------------------------------------------------------------------- diff parsing


@dataclass
class FileDiff:
    """One file of a git diff."""

    path: str
    status: str = "modified"  # added | deleted | modified
    binary: bool = False
    added: list[tuple[int, str]] = field(default_factory=list)  # (new line number, text)
    removed: list[str] = field(default_factory=list)

    @property
    def changed_lines(self) -> int:
        return len(self.added) + len(self.removed)


_DIFF_HEADER = re.compile(r"^diff --git a/(.+) b/(.+)$")
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _split_paths(rest: str) -> str:
    """The path in `a/<p> b/<p>` (the two are equal because renames are off in QEDCode diffs)."""
    both = rest[len("a/") :]
    half = (len(both) - len(" b/")) // 2
    candidate = both[:half]
    return candidate if both == f"{candidate} b/{candidate}" else both.split(" b/")[0]


_C_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}


def _read_quoted(text: str) -> str:
    """Decode the C-style quoted name git prints for unusual paths (`"a/x\\"y"` -> `a/x"y`)."""
    out = bytearray()
    i = 1  # skip the opening quote
    while i < len(text) and text[i] != '"':
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            if nxt in "01234567" and i + 3 < len(text):
                out.append(int(text[i + 1 : i + 4], 8) & 0xFF)
                i += 4
                continue
            out.append(_C_ESCAPES.get(nxt, ord(nxt) if ord(nxt) < 128 else 63))
            i += 2
            continue
        out.extend(ch.encode("utf-8", "surrogateescape"))
        i += 1
    return out.decode("utf-8", "replace")


def _header_path(rest: str) -> str:
    """The file path in the part of a `diff --git` line after `diff --git `, quoted or not."""
    if rest.startswith('"'):
        name = _read_quoted(rest)
        return name[len("a/") :] if name.startswith("a/") else name
    if ' "b/' in rest:
        return rest[len("a/") : rest.index(' "b/')]
    return _split_paths(rest)


def parse_diff(diff: str) -> list[FileDiff]:
    """Parse the output of `git diff --binary --no-renames` into per-file added/removed lines."""
    files: list[FileDiff] = []
    current: FileDiff | None = None
    new_line = 0
    in_hunk = False
    for raw in diff.split("\n"):
        line = raw.rstrip("\r")
        if line.startswith(("diff --git a/", 'diff --git "')):
            path = _header_path(line[len("diff --git ") :])
            current = FileDiff(path=path)
            files.append(current)
            in_hunk = False
            continue
        if current is None:
            continue
        if not in_hunk:
            if line.startswith("new file mode"):
                current.status = "added"
            elif line.startswith("deleted file mode"):
                current.status = "deleted"
            elif line.startswith(("Binary files ", "GIT binary patch")):
                current.binary = True
        match = _HUNK.match(line)
        if match:
            in_hunk = True
            new_line = int(match.group(1))
            continue
        if not in_hunk:
            continue
        if line.startswith("+"):
            current.added.append((new_line, line[1:]))
            new_line += 1
        elif line.startswith("-"):
            current.removed.append(line[1:])
        elif line.startswith(" "):
            new_line += 1
    return files


def count_changed_lines(diff: str) -> int:
    """Added plus removed lines in a diff (what the per-speed size limit counts)."""
    return sum(f.changed_lines for f in parse_diff(diff))


def changed_paths(diff: str) -> list[str]:
    return [f.path for f in parse_diff(diff)]


# --------------------------------------------------------------------------- scope

_SKIP = re.compile(
    r"pytest\.skip\b|pytest\.mark\.skip(?:if)?\b|pytest\.xfail\b|pytest\.mark\.xfail\b"
    r"|@unittest\.skip|unittest\.skipIf|unittest\.skipUnless|unittest\.expectedFailure|\bskipTest\("
)
_ASSERT = re.compile(r"^\s*assert\b")
# An assert that can never fail: `assert True`, `assert 1`, `assert x or True`, `assert (True)`.
_ALWAYS_TRUE = re.compile(
    r"^\s*assert\s*\(?\s*(?:True|1)\s*\)?\s*(?:$|,|#|\bor\b)"
    r"|^\s*assert\b.*\bor\s+True\s*\)?\s*(?:$|,|#)"
)
# JavaScript/TypeScript: skipped, todo or "only" tests, and removed assertions
_JS_SKIP = re.compile(
    r"\b(?:it|test|describe|suite)\.(?:skip|todo|only)\b|\b(?:xit|xtest|xdescribe|fit|fdescribe)\s*\("
    r"|\bt\.(?:skip|todo)\(|\bskip\s*:\s*true\b"
)
_JS_ASSERT = re.compile(
    r"^\s*(?:await\s+)?(?:expect\s*\(|assert\s*[.(]|t\.(?:assert|is|deepEqual)|should\.)"
)
_JS_ALWAYS_TRUE = re.compile(
    r"^\s*(?:await\s+)?(?:"
    r"expect\s*\(\s*(?:true|1)\s*\)\s*\.\s*(?:toBe\s*\(\s*(?:true|1)\s*\)|toBeTruthy\s*\(\s*\))"
    r"|assert(?:\.ok)?\s*\(\s*(?:true|1)\s*[,)]"
    r"|t\.(?:assert|true|truthy)\s*\(\s*(?:true|1)\s*[,)])"
)


def _is_test_path(path: str) -> bool:
    return is_test_file(path) or path.endswith("conftest.py")


def _weakening(file: FileDiff, *, always_true_asserts: bool = False) -> list[str]:
    """Ways a diff weakens a test file. `always_true_asserts` also flags asserts that can never
    fail (used for `qed check`; for generated candidates other stages already cover them)."""
    problems: list[str] = []
    if not _is_test_path(file.path):
        return problems
    js = is_js_path(file.path)
    skip, assertion = (_JS_SKIP, _JS_ASSERT) if js else (_SKIP, _ASSERT)
    always_true = _JS_ALWAYS_TRUE if js else _ALWAYS_TRUE
    comment = ("//", "/*", "*") if js else ("#",)
    for number, text in file.added:
        if text.lstrip().startswith(comment):
            continue
        if skip.search(text):
            problems.append(f"{file.path}:{number} adds a skip or xfail ({text.strip()[:60]})")
        elif always_true_asserts and always_true.search(text):
            problems.append(
                f"{file.path}:{number} adds an assert that can never fail ({text.strip()[:60]})"
            )
    removed = sum(1 for t in file.removed if assertion.match(t))
    added = sum(1 for _, t in file.added if assertion.match(t))
    if file.status == "modified" and removed > added:
        problems.append(f"{file.path} removes {removed - added} assert line(s) (tests weakened)")
    return problems


def scope_check(
    diff: str,
    project: ProjectConfig,
    role: str,
    speed: Speed | str,
    *,
    allowed_added: Iterable[str] = (),
    allow_protected: bool = False,
    appended: Mapping[str, Iterable[str]] | None = None,
) -> CheckResult:
    """Stage `scope`: the path rules, applied to the diff.

    `allowed_added` are files QEDCode itself added (the repro test). `appended` are existing test
    files that only gained new tests (see verify/test_additions.py); they are evidence, not a
    protected change. For role "user" (qed check) nothing is rejected: the problems are only
    reported, because the changes are the user's own, and weakened tests are marked so the grade
    function can stop counting the tests as evidence.
    """
    files = parse_diff(diff)
    allowed = set(allowed_added)
    only_added = set(appended or {})
    problems: list[str] = []
    protected_changes: list[str] = []
    weakened: list[str] = []
    for f in files:
        bad_path = validate_path(f.path)
        if bad_path:
            problems.append(f"{f.path}: {bad_path}.")
            continue
        if f.path in allowed and f.status == "added":
            problems.extend(_weakening(f))
            continue
        new_test = (f.status == "added" and is_test_file(f.path)) or (
            f.path in only_added
        )  # a new test is evidence, not tampering
        if is_protected(f.path, project.protected) and not new_test:
            protected_changes.append(f.path)
            if not allow_protected:
                problems.append(
                    f"{f.path} is a protected file (existing tests and test configuration "
                    "cannot be changed). Change the code under test instead."
                )
        if f.status == "deleted":
            problems.append(f"{f.path} was deleted; files may not be deleted.")
        if f.status == "added" and is_test_file(f.path) and role not in ("test_writer", "user"):
            # a model try may not add tests; the user's own new test is evidence (qed check)
            problems.append(f"{f.path} is a new test file; only the test writer may add tests.")
        weak = _weakening(f, always_true_asserts=role == "user")
        if weak and f.path not in weakened:
            weakened.append(f.path)
        problems.extend(weak)
    limit = max_changed_lines(speed)
    total = sum(f.changed_lines for f in files)
    if total > limit:
        problems.append(
            f"The change touches {total} lines but the limit at this speed is {limit}. "
            "Make a smaller change."
        )
    marker = [f"{PROTECTED_MARKER} {', '.join(protected_changes)}"] if protected_changes else []
    if role == "user" and weakened:
        marker.append(f"{WEAKENED_MARKER} {', '.join(weakened)}")
    details = "\n".join(marker + problems)[:DETAILS_LIMIT]
    if role == "user":
        summary = (
            f"{len(problems)} thing(s) to look at in your change (not blocking)."
            if problems
            else "Scope: nothing unusual in the change."
        )
        check = CheckResult(stage="scope", passed=True, summary=summary, details=details)
        check.details = details
        return check
    if problems:
        return CheckResult(
            stage="scope",
            passed=False,
            summary=f"Rejected: {problems[0]}"[:SUMMARY_LIMIT],
            details=details,
        )
    return CheckResult(
        stage="scope",
        passed=True,
        summary=f"{len(files)} file(s), {total} changed line(s), all within scope.",
    )


def protected_changes_in(diff: str, project: ProjectConfig) -> list[str]:
    """Protected files a diff touches (listed in receipts; grade A is capped when non-empty)."""
    return [f.path for f in parse_diff(diff) if is_protected(f.path, project.protected)]


# --------------------------------------------------------------------------- risk

RISKY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("os.system(", re.compile(r"\bos\.system\(")),
    ("subprocess", re.compile(r"\bsubprocess\b")),
    ("shutil.rmtree(", re.compile(r"\bshutil\.rmtree\(")),
    ("os.remove(", re.compile(r"\bos\.remove\(")),
    ("os.unlink(", re.compile(r"\bos\.unlink\(")),
    ("os.rmdir(", re.compile(r"\bos\.rmdir\(")),
    ("socket", re.compile(r"\bsocket\b")),
    ("requests.", re.compile(r"\brequests\.")),
    ("urllib", re.compile(r"\burllib\b")),
    ("httpx.", re.compile(r"\bhttpx\.")),
    ("eval(", re.compile(r"(?<![\w.])eval\(")),
    ("exec(", re.compile(r"(?<![\w.])exec\(")),
    ("__import__(", re.compile(r"__import__\(")),
    (
        "open() with an absolute path",
        re.compile(r"(?<![\w.])open\(\s*[rbuf]*[\"'](?:/|[A-Za-z]:[\\/])"),
    ),
    ("Path.home(", re.compile(r"\bPath\.home\(")),
]


JS_RISKY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("child_process", re.compile(r"\bchild_process\b")),
    ("execSync/spawnSync", re.compile(r"\b(?:execSync|spawnSync|execFileSync|execFile)\(")),
    ("eval(", re.compile(r"(?<![\w.])eval\(")),
    ("new Function(", re.compile(r"\bnew\s+Function\(")),
    (
        "fs delete",
        re.compile(r"\bfs(?:\.promises)?\.(?:rm|rmSync|unlink|unlinkSync|rmdir|rmdirSync)\("),
    ),
    ("fetch(", re.compile(r"(?<![\w.])fetch\(")),
    ("XMLHttpRequest", re.compile(r"\bXMLHttpRequest\b")),
    ("axios", re.compile(r"\baxios\b")),
    ("WebSocket", re.compile(r"\bWebSocket\b")),
    ("network module", re.compile(r"[\"'](?:node:)?(?:https?|net|dgram|tls|http2)[\"']")),
    ("process.exit(", re.compile(r"\bprocess\.(?:exit|kill|chdir)\(")),
    ("os.homedir(", re.compile(r"\bos\.homedir\(")),
]


@dataclass(frozen=True)
class RiskFinding:
    pattern: str
    path: str
    line: int
    text: str

    def describe(self) -> str:
        return f"{self.pattern} in {self.path}"


def risk_findings(diff: str) -> list[RiskFinding]:
    """Every risky pattern in the ADDED lines of Python, JavaScript and TypeScript files."""
    found: list[RiskFinding] = []
    for f in parse_diff(diff):
        js = is_js_path(f.path)
        if not (f.path.endswith(".py") or js):
            continue
        patterns = JS_RISKY_PATTERNS if js else RISKY_PATTERNS
        comment = ("//", "/*", "*") if js else ("#",)
        for number, text in f.added:
            if text.lstrip().startswith(comment):
                continue
            for name, pattern in patterns:
                if pattern.search(text):
                    found.append(RiskFinding(name, f.path, number, text.strip()))
    return found


def risk_check(diff: str) -> CheckResult:
    """Stage `risk`: dangerous calls in added lines. Finding any sets needs_approval.

    The result is `passed=False` until someone approves (the controller flips it after a yes, or
    when --allow-risky is set); `needs_approval` stays True so grade A is capped at B.
    """
    findings = risk_findings(diff)
    if not findings:
        return CheckResult(stage="risk", passed=True, summary="No risky patterns in the new code.")
    lines = [f"{f.path}:{f.line} {f.pattern}: {f.text[:100]}" for f in findings]
    return CheckResult(
        stage="risk",
        passed=False,
        needs_approval=True,
        summary=f"Risky pattern: {findings[0].describe()}"[:SUMMARY_LIMIT],
        details=("Risky patterns need your approval:\n" + "\n".join(lines))[:DETAILS_LIMIT],
    )
