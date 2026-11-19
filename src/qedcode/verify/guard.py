"""The `scope` and `risk` stages (AGENTS.md section 8): cheap checks on the diff, before any test.

scope: protected paths, paths outside the repo, deleted files, test files created by a coder,
       weakened tests (added skip/xfail, removed asserts) and too many changed lines.
risk:  a regex scan of ADDED Python lines for dangerous calls; finding one sets needs_approval.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from qedcode.config import ProjectConfig
from qedcode.engine.budget import max_changed_lines
from qedcode.models.schemas import CheckResult, Speed
from qedcode.project.paths import is_protected, is_test_file, validate_path

SUMMARY_LIMIT = 300
DETAILS_LIMIT = 4000
PROTECTED_MARKER = "Protected files changed:"


def protected_from_checks(checks: Iterable[CheckResult]) -> list[str]:
    """Protected files a scope check recorded as changed (the grade function reads this)."""
    for check in checks:
        if check.stage != "scope":
            continue
        for line in check.details.split("\n"):
            if line.startswith(PROTECTED_MARKER):
                return [p.strip() for p in line[len(PROTECTED_MARKER) :].split(",") if p.strip()]
    return []


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


def parse_diff(diff: str) -> list[FileDiff]:
    """Parse the output of `git diff --binary --no-renames` into per-file added/removed lines."""
    files: list[FileDiff] = []
    current: FileDiff | None = None
    new_line = 0
    in_hunk = False
    for raw in diff.split("\n"):
        line = raw.rstrip("\r")
        if line.startswith("diff --git a/"):
            path = _split_paths(line[len("diff --git ") :])
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


def _is_test_path(path: str) -> bool:
    return is_test_file(path) or path.endswith("conftest.py")


def _weakening(file: FileDiff) -> list[str]:
    problems: list[str] = []
    if not _is_test_path(file.path):
        return problems
    for number, text in file.added:
        if _SKIP.search(text) and not text.lstrip().startswith("#"):
            problems.append(f"{file.path}:{number} adds a skip or xfail ({text.strip()[:60]})")
    removed = sum(1 for t in file.removed if _ASSERT.match(t))
    added = sum(1 for _, t in file.added if _ASSERT.match(t))
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
) -> CheckResult:
    """Stage `scope`: the path rules, applied to the diff.

    `allowed_added` are files QEDCode itself added (the repro test). For role "user" (qed check)
    nothing is rejected: the problems are only reported, because the changes are the user's own.
    """
    files = parse_diff(diff)
    allowed = set(allowed_added)
    problems: list[str] = []
    protected_changes: list[str] = []
    for f in files:
        bad_path = validate_path(f.path)
        if bad_path:
            problems.append(f"{f.path}: {bad_path}.")
            continue
        if f.path in allowed and f.status == "added":
            problems.extend(_weakening(f))
            continue
        if is_protected(f.path, project.protected):
            protected_changes.append(f.path)
            if not allow_protected:
                problems.append(
                    f"{f.path} is a protected file (existing tests and test configuration "
                    "cannot be changed). Change the code under test instead."
                )
        if f.status == "deleted":
            problems.append(f"{f.path} was deleted; files may not be deleted.")
        if f.status == "added" and is_test_file(f.path) and role != "test_writer":
            problems.append(f"{f.path} is a new test file; only the test writer may add tests.")
        problems.extend(_weakening(f))
    limit = max_changed_lines(speed)
    total = sum(f.changed_lines for f in files)
    if total > limit:
        problems.append(
            f"The change touches {total} lines but the limit at this speed is {limit}. "
            "Make a smaller change."
        )
    marker = [f"{PROTECTED_MARKER} {', '.join(protected_changes)}"] if protected_changes else []
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


@dataclass(frozen=True)
class RiskFinding:
    pattern: str
    path: str
    line: int
    text: str

    def describe(self) -> str:
        return f"{self.pattern} in {self.path}"


def risk_findings(diff: str) -> list[RiskFinding]:
    """Every risky pattern in the ADDED lines of Python files in `diff`."""
    found: list[RiskFinding] = []
    for f in parse_diff(diff):
        if not f.path.endswith(".py"):
            continue
        for number, text in f.added:
            if text.lstrip().startswith("#"):
                continue
            for name, pattern in RISKY_PATTERNS:
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
