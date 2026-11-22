"""The six roles one model plays (AGENTS.md section 9): intake, locator, test_writer, coder,
repairer, explainer.

Each role builds a short prompt with one job and an exact reply format, calls
`ModelClient.chat(..., tag=<role>)`, parses the reply, does ONE format re-ask when parsing fails,
and returns a typed result. Every prompt and reply is saved under .qed/runs/<task id>/. Roles never
decide grades.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from qedcode.config import ProjectConfig
from qedcode.edits.protocol import parse_reply, problem_text, reply_ok
from qedcode.models.schemas import (
    Candidate,
    Event,
    ParsedReply,
    Speed,
    TaskKind,
    TaskSpec,
)
from qedcode.project.context import ContextFile, files_block, fit_files, task_keywords
from qedcode.project.paths import is_js_path, normalize_path, validate_path
from qedcode.project.tokens import estimate_tokens, prompt_budget
from qedcode.runtime.client import ModelClient
from qedcode.verify.cascade import Cancelled

ROLE_SETTINGS: dict[str, tuple[float | None, int]] = {
    # role: (temperature, max output tokens); None = taken from the speed schedule
    "intake": (0.1, 300),
    "locator": (0.1, 200),
    "test_writer": (0.3, 1200),
    "coder": (None, 2000),
    "repairer": (0.3, 2000),
    "explainer": (0.3, 250),
    "safety_net": (0.3, 1500),
    "hunter": (0.5, 1500),
}
CODER_MAX_TOKENS_SURE = 3000
MAX_ACCEPTANCE = 4
MAX_TEST_SNIPPET_LINES = 40
CODE_SLOT = "\x00code-slot\x00"  # swapped for the files after the budget is known
FILES_SLOT = "\x00files-slot\x00"
NO_HITS = "(no search hits)"
NO_TESTS = "(this project has no existing tests to copy from)"


def load_prompt(name: str) -> str:
    """Read a prompt template from engine/prompts/<name>.txt."""
    return resources.files("qedcode.engine").joinpath("prompts", f"{name}.txt").read_text("utf-8")


def format_prompt(name: str, **values: str) -> str:
    """Fill a template. Values are inserted as they are (braces in code are never re-parsed)."""
    template = load_prompt(name).rstrip("\n")
    return template.format(**values)


# --------------------------------------------------------------------------- typed results


@dataclass
class IntakeResult:
    kind: TaskKind = TaskKind.OTHER
    acceptance: list[str] = field(default_factory=list)
    question: str | None = None
    ok: bool = True  # False when the reply could not be understood even after the re-ask


@dataclass
class TestWriterResult:
    __test__ = False  # not a pytest class

    path: str
    content: str
    reason: str = ""


@dataclass
class CoderResult:
    reply: ParsedReply
    raw: str = ""
    ok: bool = True  # False when the reply had no usable edits even after the re-ask


# --------------------------------------------------------------------------- parsers


def parse_intake(text: str) -> tuple[IntakeResult | None, str]:
    """Parse the intake reply; returns (result, problem). problem is '' when it is fine."""
    kind_match = re.search(r"(?im)^\s*KIND\s*:\s*([A-Za-z]+)", text)
    if not kind_match:
        return None, "the KIND: line is missing"
    word = kind_match.group(1).lower()
    try:
        kind = TaskKind(word)
    except ValueError:
        options = ", ".join(k.value for k in TaskKind)
        return None, f"KIND must be one of: {options}"
    acceptance: list[str] = []
    block = re.search(r"(?is)ACCEPTANCE\s*:(.*?)(?:^\s*QUESTION\s*:|\Z)", text, re.MULTILINE)
    if block:
        for line in block.group(1).splitlines():
            m = re.match(r"^\s*(?:[-*•]|\d+[.)])\s+(.*\S)", line)
            if m:
                acceptance.append(m.group(1).strip())
    question = None
    q = re.search(r"(?im)^\s*QUESTION\s*:\s*(.*)$", text)
    if q:
        value = q.group(1).strip()
        if value and value.upper().strip(".") not in ("NONE", "N/A", "NO"):
            question = value
    return IntakeResult(kind, acceptance[:MAX_ACCEPTANCE], question), ""


def parse_locator(text: str, max_files: int) -> tuple[list[str], str]:
    """Parse `FILE: path` lines (also bullets and bare paths); returns (paths, problem)."""
    found: list[str] = []
    for line in text.splitlines():
        stripped = line.strip().strip("`*-").strip()
        match = re.match(r"(?i)^FILE\s*:\s*(.+)$", stripped)
        candidate = match.group(1) if match else stripped
        candidate = candidate.strip().strip("`'\"").strip()
        if match or re.fullmatch(r"[\w./\\-]+\.\w+", candidate):
            path = normalize_path(candidate.replace("\\", "/"))
            if path and path not in found:
                found.append(path)
    if not found:
        return [], "no FILE: lines were found"
    return found[:max_files], ""


_JS_TEST = re.compile(r"(?m)^\s*(?:test|it)(?:\.\w+)?\s*\(")


def parse_test_writer(text: str, test_path: str) -> tuple[TestWriterResult | None, str]:
    """The reply must create one test file; its path is replaced by the one QEDCode chose."""
    reply = parse_reply(text)
    edits = [e for e in reply.edits if e.op == "create_file"]
    if not edits:
        problem = problem_text(reply) or "there is no EDIT create_file block"
        return None, problem
    code = edits[0].code
    if is_js_path(test_path):
        if not _JS_TEST.search(code):
            return None, "the test file must contain at least one test(...) or it(...) call"
    elif not re.search(r"(?m)^\s*(?:async\s+)?def\s+test_\w*\(", code):
        return None, "the test file must define at least one function named test_..."
    reason = reply.extra.get("FAILS NOW BECAUSE", "")
    return TestWriterResult(test_path, code, reason), ""


# --------------------------------------------------------------------------- the runner


@dataclass(frozen=True)
class Stack:
    """The words of the prompts that depend on the project's language and test runner."""

    project: str = "Python, tests run with pytest."
    test_kind: str = "pytest"
    test_rules: str = (
        "no network, no subprocess, no files written outside pytest's tmp_path; "
        "every test has at least one assert."
    )
    fence: str = "python"
    cheatsheet: str = "protocol_cheatsheet"
    intake_template: str = "intake"  # the Python templates stay word for word as in the brief
    test_writer_template: str = "test_writer"


PYTHON_STACK = Stack()
_JS_RUNNERS = {
    "node": ("the built-in node:test runner", "node:test",
             "Use `import test from 'node:test'` and `import assert from 'node:assert/strict'`."),
    "vitest": ("Vitest", "Vitest",
               "Use `import { describe, it, expect } from 'vitest'`."),
    "jest": ("Jest", "Jest",
             "Use Jest's global `describe`, `it` and `expect` (do not import them)."),
}  # fmt: skip


def stack_for(project: ProjectConfig | None, test_path: str = "") -> Stack:
    """The prompt wording for a project (Python unless it is a JavaScript/TypeScript project)."""
    if project is None or project.language != "javascript":
        return PYTHON_STACK
    label, kind, usage = _JS_RUNNERS.get(project.test_runner, _JS_RUNNERS["node"])
    typescript = test_path.endswith((".ts", ".tsx", ".mts", ".cts"))
    return Stack(
        project=f"JavaScript/TypeScript (Node.js), tests run with {label}.",
        test_kind=f"{kind} test",
        test_rules=(
            f"{usage} Import relative files with their extension as the existing tests do; "
            "no network, no child processes, no files written outside the system temp folder; "
            "every test has at least one assertion."
        ),
        fence="typescript" if typescript else "javascript",
        cheatsheet="protocol_cheatsheet_js",
        intake_template="intake_js",
        test_writer_template="test_writer_js",
    )


class Roles:
    """Runs the six roles against one ModelClient and records every prompt and reply."""

    def __init__(
        self,
        client: ModelClient,
        run_dir: Path | None = None,
        *,
        on_event: Callable[[Event], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
        on_token: Callable[[str, str], None] | None = None,
        project: ProjectConfig | None = None,
    ) -> None:
        self.project = project
        self.client = client
        self.run_dir = Path(run_dir) if run_dir else None
        self._on_event = on_event
        self._cancelled = cancelled
        self._on_token = on_token
        self._seq = 0
        self.calls = 0

    # ------------------------------------------------------------------ plumbing

    def _emit(self, kind: str, message: str, **data) -> None:
        if self._on_event is not None:
            self._on_event(Event(kind=kind, message=message, data=data))  # type: ignore[arg-type]

    def _save(self, tag: str, label: str, kind: str, text: str) -> None:
        if self.run_dir is None:
            return
        self._seq += 1
        name = f"{self._seq:02d}-{tag}{'-' + label if label else ''}-{kind}.txt"
        try:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            (self.run_dir / name).write_text(text, encoding="utf-8")
        except OSError:
            pass  # a log we cannot write must never stop the task

    @staticmethod
    def _render(messages: list[dict]) -> str:
        return "\n\n".join(f"[{m['role']}]\n{m['content']}" for m in messages)

    def _chat(
        self, messages: list[dict], tag: str, label: str, temperature: float, max_tokens: int
    ) -> str:
        if self._cancelled and self._cancelled():
            raise Cancelled

        def piece(text: str) -> None:
            if self._cancelled and self._cancelled():
                raise Cancelled
            if self._on_token is not None:
                self._on_token(tag, text)

        self.calls += 1
        return self.client.chat(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tag=tag,
            on_token=piece,
        )

    def _ask(
        self,
        tag: str,
        prompt: str,
        *,
        temperature: float,
        max_tokens: int,
        parse: Callable[[str], tuple[object, str]],
        label: str = "",
        reask: bool = True,
    ) -> tuple[object, str, str]:
        """One role call: returns (parsed, problem, raw reply). Re-asks once on a bad format."""
        system = load_prompt("system").strip()
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        self._save(tag, label, "prompt", self._render(messages))
        raw = self._chat(messages, tag, label, temperature, max_tokens)
        self._save(tag, label, "reply", raw)
        parsed, problem = parse(raw)
        if not problem or not reask:
            return parsed, problem, raw
        self._emit("warning", f"The {tag} reply had the wrong format ({problem}); asking again.")
        messages += [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": format_prompt("reask", problem=problem)},
        ]
        self._save(tag, label, "reask-prompt", messages[-1]["content"])
        raw = self._chat(messages, tag, label, temperature, max_tokens)
        self._save(tag, label, "reask-reply", raw)
        parsed, problem = parse(raw)
        return parsed, problem, raw

    def _budget(self, max_tokens: int) -> int:
        return prompt_budget(self.client.context_tokens, max_tokens)

    # ------------------------------------------------------------------ intake

    def intake(self, request: str, speed: Speed = Speed.NORMAL) -> IntakeResult:
        """Classify the task and list up to four checkable acceptance lines (may ask a question)."""
        temperature, max_tokens = ROLE_SETTINGS["intake"]
        parsed, problem, _ = self._ask(
            "intake",
            format_prompt(
                stack_for(self.project).intake_template,
                request=request,
                project=stack_for(self.project).project,
            ),
            temperature=temperature or 0.1,
            max_tokens=max_tokens,
            parse=parse_intake,
        )
        if problem or parsed is None:
            self._emit("warning", f"Could not understand the intake reply ({problem}); continuing.")
            return IntakeResult(ok=False)
        return parsed  # type: ignore[return-value]

    # ------------------------------------------------------------------ locator

    def locator(
        self,
        spec: TaskSpec,
        repo_map: str,
        hits: str,
        max_files: int,
    ) -> list[str]:
        """Pick the files to read or change; [] on failure (the caller uses the search hits)."""
        temperature, max_tokens = ROLE_SETTINGS["locator"]
        prompt = format_prompt(
            "locator",
            request=spec.request,
            acceptance=_bullets(spec.acceptance),
            repo_map=repo_map or "(empty)",
            hits=hits or NO_HITS,
            max_files=str(max_files),
        )
        parsed, problem, _ = self._ask(
            "locator",
            prompt,
            temperature=temperature or 0.1,
            max_tokens=max_tokens,
            parse=lambda text: parse_locator(text, max_files),
        )
        return [] if problem else list(parsed)  # type: ignore[arg-type]

    # ------------------------------------------------------------------ test writer

    def test_writer(
        self,
        spec: TaskSpec,
        code_files: list[ContextFile],
        example_test: ContextFile | None,
        test_path: str,
        *,
        label: str = "",
    ) -> TestWriterResult | None:
        """Write ONE new test file that should fail now; None when no usable file came back."""
        temperature, max_tokens = ROLE_SETTINGS["test_writer"]
        snippet = NO_TESTS
        example_path = "no example"
        if example_test is not None:
            example_path = example_test.path
            snippet = "\n".join(example_test.content.splitlines()[:MAX_TEST_SNIPPET_LINES])
        stack = stack_for(self.project, test_path)
        fixed = format_prompt(
            stack.test_writer_template,
            request=spec.request,
            acceptance=_bullets(spec.acceptance),
            code_context=CODE_SLOT,
            example_test_path=example_path,
            example_test_snippet=snippet,
            test_path=test_path,
            test_kind=stack.test_kind,
            test_rules=stack.test_rules,
            fence=stack.fence,
        )
        room = self._budget(max_tokens) - estimate_tokens(fixed)
        fitted = fit_files(code_files, max(room, 256), task_keywords(spec.request))
        prompt = fixed.replace(CODE_SLOT, files_block(fitted), 1)
        parsed, problem, _ = self._ask(
            "test_writer",
            prompt,
            temperature=temperature or 0.3,
            max_tokens=max_tokens,
            parse=lambda text: parse_test_writer(text, test_path),
            label=label,
        )
        return None if problem else parsed  # type: ignore[return-value]

    def safety_net(
        self,
        symbol: str,
        path: str,
        code_files: list[ContextFile],
        example_test: ContextFile | None,
        test_path: str,
        *,
        label: str = "",
    ) -> TestWriterResult | None:
        """Write ONE test file that PASSES on today's code (AGENTS.md 15.11); None when unusable."""
        return self._symbol_tests(
            "safety_net", symbol, path, code_files, example_test, test_path, label
        )

    def hunter(
        self,
        symbol: str,
        path: str,
        code_files: list[ContextFile],
        example_test: ContextFile | None,
        test_path: str,
        *,
        label: str = "",
    ) -> TestWriterResult | None:
        """Write tests of the CORRECT behaviour where a bug is suspected (AGENTS.md 15.12)."""
        return self._symbol_tests(
            "hunter", symbol, path, code_files, example_test, test_path, label
        )

    def _symbol_tests(
        self,
        role: str,
        symbol: str,
        path: str,
        code_files: list[ContextFile],
        example_test: ContextFile | None,
        test_path: str,
        label: str,
    ) -> TestWriterResult | None:
        temperature, max_tokens = ROLE_SETTINGS[role]
        snippet, example_path = NO_TESTS, "no example"
        if example_test is not None:
            example_path = example_test.path
            snippet = "\n".join(example_test.content.splitlines()[:MAX_TEST_SNIPPET_LINES])
        stack = stack_for(self.project, test_path)
        fixed = format_prompt(
            role,
            symbol=symbol,
            path=path,
            code_context=CODE_SLOT,
            example_test_path=example_path,
            example_test_snippet=snippet,
            test_path=test_path,
            test_kind=stack.test_kind,
            test_rules=stack.test_rules,
            fence=stack.fence,
        )
        room = self._budget(max_tokens) - estimate_tokens(fixed)
        fitted = fit_files(code_files, max(room, 256), [symbol])
        prompt = fixed.replace(CODE_SLOT, files_block(fitted), 1)
        parsed, problem, _ = self._ask(
            role,
            prompt,
            temperature=temperature or 0.3,
            max_tokens=max_tokens,
            parse=lambda text: parse_test_writer(text, test_path),
            label=label,
        )
        return None if problem else parsed  # type: ignore[return-value]

    # ------------------------------------------------------------------ coder and repairer

    def _edit_reply(self, tag: str, prompt: str, temperature: float, max_tokens: int, label: str):
        def parse(text: str) -> tuple[ParsedReply, str]:
            reply = parse_reply(text)
            return reply, "" if reply_ok(reply) else problem_text(reply)

        parsed, problem, raw = self._ask(
            tag, prompt, temperature=temperature, max_tokens=max_tokens, parse=parse, label=label
        )
        return CoderResult(reply=parsed, raw=raw, ok=not problem)  # type: ignore[arg-type]

    def coder(
        self,
        spec: TaskSpec,
        files: list[ContextFile],
        repro: str | None,
        temperature: float,
        *,
        label: str = "",
        examples: str = "",
    ) -> CoderResult:
        """Ask for the smallest change that completes the task (EDIT blocks).

        `examples` is the text of similar earlier fixes in this project (local model only)."""
        max_tokens = (
            CODER_MAX_TOKENS_SURE if spec.speed is Speed.SURE else ROLE_SETTINGS["coder"][1]
        )
        repro_block = (
            f"\nThis test must pass after your change:\n{repro.rstrip()}\n" if repro else ""
        )
        cheatsheet = load_prompt(stack_for(self.project).cheatsheet).rstrip("\n")
        fixed = format_prompt(
            "coder",
            request=spec.request,
            acceptance=_bullets(spec.acceptance),
            repro_block=repro_block,
            examples_block=f"\n{examples.rstrip()}\n" if examples else "",
            files_block=FILES_SLOT,
            protocol_cheatsheet=cheatsheet,
        )
        room = self._budget(max_tokens) - estimate_tokens(fixed)
        fitted = fit_files(files, max(room, 256), task_keywords(spec.request))
        prompt = fixed.replace(FILES_SLOT, files_block(fitted), 1)
        return self._edit_reply("coder", prompt, temperature, max_tokens, label)

    def repairer(
        self,
        spec: TaskSpec,
        near: Candidate,
        failure_text: str,
        files: list[ContextFile],
        *,
        label: str = "",
    ) -> CoderResult:
        """Fix a near-miss: sees its diff, what went wrong and the current code of changed files."""
        temperature, max_tokens = ROLE_SETTINGS["repairer"]
        cheatsheet = load_prompt(stack_for(self.project).cheatsheet).rstrip("\n")
        fixed = format_prompt(
            "repairer",
            request=spec.request,
            acceptance=_bullets(spec.acceptance),
            diff=near.diff.rstrip("\n"),
            failure_text=failure_text.strip() or "(no details)",
            files_block=FILES_SLOT,
            protocol_cheatsheet=cheatsheet,
        )
        room = self._budget(max_tokens) - estimate_tokens(fixed)
        fitted = fit_files(files, max(room, 256), task_keywords(spec.request))
        prompt = fixed.replace(FILES_SLOT, files_block(fitted), 1)
        return self._edit_reply("repairer", prompt, temperature or 0.3, max_tokens, label)

    # ------------------------------------------------------------------ explainer

    def explainer(self, spec: TaskSpec, diff: str, checks_summary: str, files: list[str]) -> str:
        """2 to 4 plain sentences about the change; a template sentence if the model fails."""
        temperature, max_tokens = ROLE_SETTINGS["explainer"]
        prompt = format_prompt(
            "explainer", request=spec.request, diff=diff.rstrip("\n"), checks_summary=checks_summary
        )
        try:
            parsed, problem, raw = self._ask(
                "explainer",
                prompt,
                temperature=temperature or 0.3,
                max_tokens=max_tokens,
                parse=lambda text: (text.strip(), "" if text.strip() else "the reply was empty"),
                reask=False,
            )
        except Cancelled:
            raise
        except Exception:  # noqa: BLE001 - the explanation is a courtesy, never a blocker
            return fallback_explanation(files, checks_summary)
        text = str(parsed).strip()
        return text if text and not problem else fallback_explanation(files, checks_summary)


def _bullets(lines: list[str]) -> str:
    return "\n".join(f"- {line}" for line in lines) if lines else "- (none given)"


def fallback_explanation(files: list[str], checks_summary: str) -> str:
    """The sentence used when the explainer model gives nothing usable."""
    where = ", ".join(files[:3]) if files else "your project"
    return (
        f"QEDCode changed {where}. The change was tested; here is what the checks found: "
        f"{checks_summary}. Open the diff to see exactly what changed."
    )


def reply_paths_are_valid(reply: ParsedReply, root: Path) -> list[str]:
    """Paths in a reply that fail the basic path rules (used for early, friendly warnings)."""
    return [e.path for e in reply.edits if validate_path(e.path, root)]
