# QEDCode — AI Build Brief

Nov 20, 2026 · @Susamanth Nellore

## 1. Mission and scope

QEDCode is a local-first coding engine that makes small LLMs (4–6 GB VRAM) reliable. It generates several candidate patches, verifies each with deterministic checks, and returns only the best verified patch with an evidence grade (A–D) and a re-runnable proof receipt.

**How to use this file.** Save it in the repo root as `AGENTS.md` (and a `CLAUDE.md` that says "Read AGENTS.md"). Read it fully before writing code. It is the source of truth; when a task prompt conflicts with it, stop and ask the human.

**The product in one paragraph.** The user runs `qed` inside a Python project folder and a Textual terminal UI opens. The user types a task. The engine snapshots the project into a git worktree, finds relevant files, optionally writes a reproduction test, asks one resident model for k candidate edits, applies each in isolation, and runs a verification cascade (apply, parse, scope, risk, lint, tests). It repairs near-misses, selects the best candidate, grades it, writes a receipt and explains it in plain English. Nothing touches the user's files until they press apply. The same engine is exposed as an MCP server (`qed mcp`) so Claude Code, OpenCode and other agents can call it.

**v1 scope — build exactly this:**

| Area | v1 |
| --- | --- |
| Languages | Python projects tested with pytest |
| Interfaces | `qed` TUI, CLI subcommands, MCP server over stdio |
| Model backends | llama.cpp `llama-server` (auto-installed), Ollama, any OpenAI-compatible local server (LM Studio), cloud via LiteLLM |
| Modes | local, mix (local first; helper when stuck), cloud |
| Helper (mix) | a cloud model, or a second model served by Ollama or another OpenAI-compatible server |
| Hardware | detect NVIDIA, AMD, Intel, Apple Silicon, CPU-only; user can override |
| Engine | snapshot + worktrees, intake, locator, test writer, k candidates, repair, selection, grade, receipt, explanation |
| Checks | AST parse, scope and tamper guard, risky-pattern guard, ruff fatal rules (new errors only), pytest targeted + full suite |
| Speed dial | fast, normal, sure |
| Safety | worktree isolation, fixed command allowlist, env scrubbing, timeouts, ask before any download |

**Out of scope for v1 — do not build, not even partially:** other languages or tree-sitter, Docker sandbox, memory of past fixes, Bug Hunter, Night Shift, Watch Mode, Safety Net Builder, web dashboard, swapping in a big local MoE helper through llama-server, LoRA training, learned routing, telemetry or leaderboard, pull-request integration, TRM. Leave the clean extension points named in this file instead.

## 2. Non-negotiable rules for the AI building this

Breaking any rule below is a bug, even if the feature works.

1. **Engine purity.** `engine/`, `verify/`, `edits/`, `vcs/`, `project/`, `runtime/` and `hardware/` never import `tui/`, `cli.py` or `mcp_server.py`. UIs call only `qedcode.engine.api` and receive progress as `Event` objects through a callback.
2. **The model never runs commands.** Only fixed commands built by QEDCode code run: git, the project's Python with pytest, ruff, and the llama.cpp server. Never use `shell=True`. Model text is never used as a command or argument, except file paths validated to sit inside the worktree.
3. **No success without evidence.** Every result returned to any UI carries a grade computed only in `verify/grade.py` and a `Receipt`. No other code path may set or upgrade a grade.
4. **The user's working tree is read-only until apply.** All writes happen in worktrees under `.qed/worktrees/`. Only `vcs/patch.py` (`apply_to_user`, `undo_last`) writes to the user's files, and it refuses if those files changed since the snapshot.
5. **Existing tests are protected.** Candidates that edit, delete, skip or weaken protected files are rejected unless the task explicitly allows it. Only the test-writer role may add test files.
6. **Code never travels inside JSON.** Model replies use the plain-text edit protocol in section 7, with code in fenced blocks.
7. **Ask before every download or install.** Never install drivers, Docker or system packages. Everything QEDCode owns lives under `QEDCODE_HOME` (default `~/.qedcode`) or the project's `.qed/` folder.
8. **Secrets stay secret.** API keys come only from the OS keychain (`keyring`) or environment variables. They never appear in config files, logs, receipts or prompts. Project commands run with env vars matching `KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL` removed.
9. **Tests without a GPU.** Every module has unit tests. Anything that would call an LLM uses `FakeModel` in tests. CI passes with no GPU and no network.
10. **Small, scoped changes.** Touch only the files the current step names. No drive-by refactors. Renaming a public function means updating every caller and test in the same change.
11. **Cross-platform.** Windows, macOS and Linux. Use `pathlib`, quote nothing by hand, handle spaces in paths, never require bash.
12. **Errors are for humans.** Every user-facing error says what happened, why, and what to do next. Raw tracebacks go to `~/.qedcode/logs/`, never to the screen.
13. **Record what you did.** Temperatures, model ids and commands go into receipts. Update `PROGRESS.md` at the end of each step.

**Allowed stack** (adding a dependency means updating this list first):

| Purpose | Library |
| --- | --- |
| Language and packaging | Python ≥ 3.11 (target 3.12), `uv`, `hatchling` build backend |
| CLI | `typer`, `rich` |
| Terminal UI | `textual` |
| Data models | `pydantic` v2 |
| HTTP to local model servers | `httpx` |
| Cloud models only | `litellm` |
| API keys | `keyring` |
| Model downloads | `huggingface_hub` |
| Config | `tomllib` (stdlib) + `tomli-w` |
| Lint stage | `ruff` (runtime dependency, run as a subprocess) |
| Process control | `psutil` |
| MCP server | `mcp` (official Python SDK, `FastMCP`) |
| NVIDIA telemetry (optional extra) | `nvidia-ml-py` |
| Dev only | `pytest`, `pytest-asyncio`, `ruff` |

## 3. Glossary

Use these words exactly, in code names, UI text and docs.

| Term | Meaning |
| --- | --- |
| Task | One user request, e.g. "fix the login bug". Has an id like `t-20261120-153201-ab12`. |
| TaskSpec | The task after intake: kind, acceptance criteria, speed, target grade |
| Snapshot | A commit capturing the user's project exactly as it is now, including uncommitted and untracked (non-ignored) files. Kept under `refs/qed/snapshots/<task id>`. |
| Worktree | A `git worktree` checked out from the snapshot (or a candidate) under `.qed/worktrees/` where one candidate is applied and checked |
| Candidate (try) | One proposed change from the model: parsed edits, its worktree, its commit, its checks, its grade |
| Role | A prompt template the same model plays: intake, locator, test\_writer, coder, repairer, explainer |
| Edit block | One `EDIT ...` unit in a model reply (section 7) |
| Cascade | The ordered checks run on each candidate, cheapest first (section 8) |
| Stage | One step of the cascade: apply, parse, scope, risk, lint, fbpa, targeted, full |
| Baseline | Test outcomes on the snapshot before any change; used to spot regressions and known failures |
| Known failure | A test that already fails on the snapshot; never blamed on a candidate |
| Repro test | A new test file written by the test-writer role that fails on the snapshot and should pass after the fix |
| FBPA | Fail-before-pass-after: a test that fails on the snapshot and passes with the candidate. Required for grade A. |
| Targeted tests | The repro test plus existing tests related to the changed files |
| Full suite | All tests in the project |
| Protected paths | Files candidates may not touch: existing tests and test config (section 8) |
| Grade | A, B, C or D for an accepted candidate; `None` means rejected (section 8) |
| Receipt | JSON proof of a result that `qed verify` can re-run (section 8) |
| Speed | fast, normal or sure: how hard the engine tries (section 6) |
| Mode | local, mix or cloud: where the model runs |
| Helper | The stronger model used in mix mode when the local model is stuck |
| Backend | How a model is reached: `llamacpp`, `ollama`, `openai_compat`, `cloud`, `fake` |
| Model server | The running process that serves a local model (`llama-server` or Ollama) |
| Tier | A hardware class by usable VRAM: 4 GB, 6 GB, 8 GB, 12 GB+, Apple, CPU |
| Bug zoo | Tiny buggy Python projects in `bugzoo/` used to test the engine end to end |
| FakeModel | A scripted `ModelClient` that returns canned replies per role, for tests |
| Event | A progress message from the engine to a UI (section 5) |

## 4. Architecture

Three thin front doors (TUI, CLI, MCP) call one engine API; the engine drives deterministic modules and talks to models only through a `ModelClient` interface. The diagram is plain text on purpose, so AI tools can read it.

```text
  TUI (textual)      CLI (typer)      MCP server (stdio)      [later: web UI]
        \                 |                  /
         +--------> qedcode.engine.api <----+      solve | check | verify | apply | undo
                          |                       emits Event objects to a callback
                          v
  engine/solve.py  controller: speed dial, budget, stop rules, helper escalation
     |-- project/   detect commands, repo map, search      -> context for prompts
     |-- engine/roles.py   intake, locator, test_writer, coder, repairer, explainer
     |                 '--> runtime.ModelClient.chat(...)
     |-- vcs/       snapshot -> worktree per candidate -> candidate commit -> diff
     |-- edits/     parse reply (protocol) -> apply edits with Python ast
     |-- verify/    cascade: apply, parse, scope, risk, lint, fbpa, targeted, full
     |                 -> grade.py -> receipt.py
     '-- engine/select.py  pick best candidate, pick near-miss to repair

  runtime/   ModelClient backends: llamacpp | ollama | openai_compat | cloud | fake
             server manager (one shared local model server per machine)
  hardware/  detect GPU + VRAM, recommend model + context
  runtime/installer.py  download llama.cpp build and models (always asks first)
```

| Module | Owns | Must not |
| --- | --- | --- |
| `engine/api.py` | Public functions: `solve`, `check`, `verify`, `apply`, `undo`, `status` | Contain logic beyond wiring |
| `engine/solve.py` | The controller loop (section 6) | Run subprocesses directly |
| `engine/roles.py` + `engine/prompts/*.txt` | Building prompts, calling the model, parsing replies, one format re-ask | Decide grades |
| `engine/select.py` | Ranking candidates | Call the model |
| `project/` | Python interpreter, test command, protected paths, repo map, keyword search | Write files |
| `vcs/` | Snapshots, worktrees, candidate commits, diffs, apply/undo to the user's tree | Call the model |
| `edits/` | Edit protocol parser and the AST-based applier | Touch files outside the given worktree |
| `verify/` | Command runner, cascade stages, guard, grade, receipt, verify | Call the model |
| `runtime/` | Model clients, server manager, catalog, installer | Know about tasks |
| `hardware/` | Detection and recommendation | Download anything |
| `tui/`, `cli.py`, `mcp_server.py` | Presentation and user questions | Contain engine logic |

**Solve data flow, in order:**

1. UI calls `api.solve(root, request, speed, callbacks)`.
2. `vcs.snapshot` captures the project; `project.load` reads confirmed commands.
3. `roles.intake` turns the request into a `TaskSpec` (may ask one question).
4. `project.context` builds a repo map and search hits; `roles.locator` picks files.
5. `verify.baseline` runs tests on the snapshot.
6. Optional: `roles.test_writer` writes a repro test; kept only if it fails on the snapshot.
7. For each try: `roles.coder` → `edits.parse` → new worktree → `edits.apply` → commit → `verify.cascade` → `grade`.
8. Stop early when the target grade is reached; otherwise `roles.repairer` on the best near-miss, then the helper in mix mode.
9. `select.best` → full suite if required → `receipt.build` → `roles.explainer`.
10. Return `SolveResult`; the UI shows it and calls `api.apply` only when the user says so.

## 5. Data models

All shared data lives in `src/qedcode/models/schemas.py` as Pydantic v2 models. Use `Field(default_factory=list)` for list and dict defaults. Paths inside models are repo-relative with forward slashes.

```python
from enum import Enum
from typing import Literal, Protocol
from pydantic import BaseModel, Field

class Speed(str, Enum):
    FAST = "fast"; NORMAL = "normal"; SURE = "sure"

class Mode(str, Enum):
    LOCAL = "local"; MIX = "mix"; CLOUD = "cloud"

class Grade(str, Enum):           # rank: A=4 > B=3 > C=2 > D=1; add a rank() helper
    A = "A"; B = "B"; C = "C"; D = "D"

class TaskKind(str, Enum):
    BUGFIX = "bugfix"; FEATURE = "feature"; REFACTOR = "refactor"
    TEST = "test"; DOCS = "docs"; OTHER = "other"

Stage = Literal["apply", "parse", "scope", "risk", "lint", "fbpa", "targeted", "full"]
STAGE_ORDER: list[str] = ["apply", "parse", "scope", "risk", "lint", "fbpa", "targeted", "full"]

class TaskSpec(BaseModel):
    id: str                                   # "t-20261120-153201-ab12"
    request: str                              # the user's words, verbatim
    kind: TaskKind = TaskKind.OTHER
    acceptance: list[str] = Field(default_factory=list)   # max 4 checkable lines
    speed: Speed = Speed.NORMAL
    target_grade: Grade = Grade.B
    allow_test_edits: bool = False

class EditOp(BaseModel):
    op: Literal["replace_symbol", "insert_after_symbol", "add_import",
                "search_replace", "create_file"]
    path: str
    symbol: str | None = None                 # "func" or "Class.method"
    search: str | None = None                 # search_replace only
    code: str                                 # new code or replacement text

class ParsedReply(BaseModel):
    plan: list[str] = Field(default_factory=list)
    edits: list[EditOp] = Field(default_factory=list)
    extra: dict[str, str] = Field(default_factory=dict)   # e.g. FAILS NOW BECAUSE
    errors: list[str] = Field(default_factory=list)       # format problems

class TestResult(BaseModel):
    key: str                                  # "<classname>::<name>" from JUnit XML
    outcome: Literal["passed", "failed", "error", "skipped"]
    message: str = ""                         # first failure line, trimmed

class CheckResult(BaseModel):
    stage: Stage
    passed: bool
    command: list[str] | None = None
    exit_code: int | None = None
    duration_s: float = 0.0
    summary: str = ""                         # one line for humans, <= 300 chars
    details: str = ""                         # trimmed text for the repairer, <= 4000 chars
    tests: list[TestResult] = Field(default_factory=list)
    log_path: str | None = None
    needs_approval: bool = False              # risk stage only

class Candidate(BaseModel):
    id: str                                   # "c1", "c2", "r1" (repair), "h1" (helper)
    role: Literal["coder", "repairer", "helper", "user"]   # "user" = qed check
    parent_id: str | None = None
    model: str = ""
    temperature: float = 0.0
    reply: ParsedReply = Field(default_factory=ParsedReply)
    worktree: str | None = None
    commit: str | None = None
    diff: str = ""
    diff_lines: int = 0
    checks: list[CheckResult] = Field(default_factory=list)
    failed_stage: Stage | None = None
    grade: Grade | None = None                # None = rejected
    tests_passed: int = 0
    full_suite_ran: bool = False

class Baseline(BaseModel):
    commit: str
    results: dict[str, str] = Field(default_factory=dict)  # key -> outcome
    full: bool = False
    runnable: bool = True                     # False = pytest could not run at all
    reason: str = ""

class Receipt(BaseModel):
    schema_version: Literal[1] = 1
    qed_version: str
    created_at: str                           # ISO 8601, UTC
    project_name: str
    task: TaskSpec
    base_commit: str                          # snapshot or HEAD commit sha
    base_ref: str                             # "refs/qed/snapshots/<task id>" or "HEAD"
    patch: str                                # git diff --binary base..candidate
    patch_sha256: str
    tests_added: list[str] = Field(default_factory=list)
    fbpa_tests: list[str] = Field(default_factory=list)
    known_failures: list[str] = Field(default_factory=list)
    checks: list[CheckResult]
    protected_changes: list[str] = Field(default_factory=list)
    grade: Grade
    unverified: list[str] = Field(default_factory=list)  # plain-English gaps
    model_info: dict = Field(default_factory=dict)        # backend, model, ctx, temps, helper_used
    commands: list[list[str]] = Field(default_factory=list)  # what verify re-runs

class SolveResult(BaseModel):
    status: Literal["done", "no_fix", "needs_input", "cancelled", "error"]
    task: TaskSpec
    best: Candidate | None = None
    candidates: list[Candidate] = Field(default_factory=list)
    receipt_path: str | None = None
    explanation: str = ""
    question: str | None = None
    message: str = ""                         # one-line human summary

class Event(BaseModel):
    kind: Literal["stage", "candidate", "check", "model", "log", "warning", "result"]
    message: str                              # ready to show to a human
    data: dict = Field(default_factory=dict)

class Callbacks(Protocol):                    # implemented by each UI
    def on_event(self, event: Event) -> None: ...
    def ask(self, question: str) -> str | None: ...       # None = cannot ask (non-interactive)
    def confirm(self, prompt: str) -> bool: ...           # False when non-interactive
    def cancelled(self) -> bool: ...
```

`GlobalConfig` and `ProjectConfig` are specified in section 12 and live in `config.py`.

## 6. Engine algorithm

The controller tries cheap things first and stops the moment a candidate reaches the target grade. All numbers below are defaults in `engine/budget.py`, overridable in config.

**Speed dial:**

| Setting | fast | normal | sure |
| --- | --- | --- | --- |
| Candidate tries (k) | 1 | 3 | 6 |
| Temperatures, in order | 0.2 | 0.2, 0.7, 0.9 | 0.2, 0.6, 0.7, 0.8, 0.9, 1.0 |
| Repair rounds | 0 | 1 | 2 |
| Test writer | off | on for bugfix and feature | on unless kind is docs |
| Baseline | targeted tests only | full suite | full suite |
| Full suite on the finalist | no | yes | yes |
| Max files from locator | 3 | 5 | 8 |
| Max changed lines per candidate | 200 | 300 | 600 |
| Target grade | B | A if a repro test exists, else B | A |
| Helper in mix mode | never | after repairs fail, if the user confirms | after repairs fail, if the user confirms |
| Time budget | 5 min | 15 min | 30 min |

**Controller (`engine/solve.py`):**

```python
def solve(root, request, speed, mode, cb) -> SolveResult:
    with project_lock(root):                       # one job per project; else status="error", busy
        proj = project.load(root)                  # confirmed commands (section 12)
        snap = vcs.snapshot(root, task_id)         # tracked + untracked, user tree untouched
        spec = roles.intake(request, speed)        # kind, acceptance, maybe a question
        if spec_question and (ans := cb.ask(q)):   # non-interactive: continue without
            spec = roles.intake(request + "\n" + ans, speed)
        ctx = project.context(snap, spec, max_files[speed])   # locator runs inside
        base = verify.baseline(snap, proj, full=(speed != FAST), related=ctx.related_tests)
        if not base.runnable:
            cb.on_event(warning(base.reason))      # results can reach grade C or D only
        repro = None
        if test_writer_on(speed, spec.kind) and base.runnable:
            repro = roles.test_writer(spec, ctx)               # up to 2 attempts
            if repro and not verify.fails_on_base(repro, snap, proj):
                repro = None                                   # a test that passes now proves nothing
        spec.target_grade = target(speed, repro)
        pool = []
        for temp in temps[speed][:k[speed]]:
            if budget_spent() or cb.cancelled(): break
            c = roles.coder(spec, ctx, repro, temp)
            evaluate(c, base_commit=snap.commit, repro=repro, baseline=base, cb=cb)
            pool.append(c)
            if reached(c, spec.target_grade): break
        for _ in range(repair_rounds[speed]):
            if any_reached(pool) or budget_spent() or cb.cancelled(): break
            near = select.near_miss(pool)
            if near is None: break
            r = roles.repairer(spec, near, failure_text(near))
            evaluate(r, base_commit=near.commit, ...)
            pool.append(r)
            if no_progress(r, near): break
        if mode == MIX and helper_ok(speed) and not any_reached(pool):
            if cb.confirm("The local model is stuck. Ask the helper model?"):
                h = roles.coder(spec, ctx.compact(), repro, 0.2, model="helper")
                evaluate(h, base_commit=snap.commit, ...); pool.append(h)
        best = select.best(pool)
        if best is None:
            return no_fix(spec, pool)              # closest attempt + its failure + a question
        if needs_full(speed) and not best.full_suite_ran:
            verify.run_full(best, base); regrade(best)
        receipt = verify.receipt.build(spec, snap, best, base, repro)
        text = roles.explainer(spec, best)
        return SolveResult(status="done", best=best, receipt_path=receipt.path, explanation=text, ...)
```

**`evaluate(candidate)`** creates a fresh worktree at `base_commit`, writes the repro test file (if any), applies the parsed edits, commits inside the worktree, computes the diff against the snapshot, then runs the cascade stages in order (section 8). The first failing stage sets `failed_stage` and stops the cascade; otherwise `verify/grade.py` sets `grade`. Emit an `Event` after every stage.

**Selection (`engine/select.py`):**

- `best(pool)`: among candidates with a grade, sort by grade rank, then FBPA passed, then `tests_passed`, then fewer `diff_lines`, then earlier creation. Return `None` if no candidate has a grade.
- `near_miss(pool)`: among rejected candidates, prefer the one whose `failed_stage` is latest in `STAGE_ORDER`, then most tests passed, then fewer changed lines. Candidates that failed at `apply` qualify only if nothing better exists.
- `no_progress(new, parent)`: same failed stage and the same set of failing test keys, or an identical diff. Stop repairing when true.

**`check` (grade the user's own changes):** base = `HEAD` (or `--base <ref>`); candidate = a snapshot of the current working tree; role `user`. Run the cascade without the model: parse, scope (report only), risk (warning only), lint, fbpa (tests added in the diff, run against base), targeted, full when speed is not fast. Grade and write a receipt. Never apply anything.

**`verify(receipt)`:** check `patch_sha256`; make sure `base_commit` exists (else explain that verify must run in the same repo); create a worktree at `base_commit`; `git apply` the patch; re-run `receipt.commands`; compare each check's `passed` flag and every FBPA test outcome. Print `VERIFIED` or a list of mismatches.

**Cancel and budget:** check `cb.cancelled()` and the time budget between every model call and every stage. On cancel, kill running subprocesses (whole process tree), remove worktrees, return `status="cancelled"`.

## 7. Edit protocol

Models reply in plain text: an optional `PLAN:` block, one or more `EDIT` blocks, and optional key lines such as `FAILS NOW BECAUSE:`. The parser (`edits/protocol.py`) is lenient; the applier (`edits/apply.py`) is strict. Line numbers and unified diffs are never asked of the model, because small models get them wrong.

**A complete example reply:**

````text
PLAN:
- rotate() must reject a token that was already used
- add a helper that marks tokens as used

EDIT replace_symbol path=app/auth.py symbol=TokenStore.rotate
```python
def rotate(self, old_token: str) -> str:
    if old_token in self._used:
        raise TokenReused(old_token)
    self._mark_used(old_token)
    return self.issue()
```

EDIT insert_after_symbol path=app/auth.py symbol=TokenStore.rotate
```python
def _mark_used(self, token: str) -> None:
    self._used.add(token)
```

EDIT add_import path=app/auth.py
```python
from app.errors import TokenReused
```

EDIT search_replace path=app/config.py
<<<<<<< SEARCH
TOKEN_TTL = 60
=======
TOKEN_TTL = 900
>>>>>>> REPLACE

EDIT create_file path=app/errors.py
```python
class TokenReused(Exception):
    pass
```
````

**Header line** (one per edit), matched with this pattern after stripping whitespace:

```text
^EDIT\s+(replace_symbol|insert_after_symbol|add_import|search_replace|create_file)\s+path=(\S+)(?:\s+symbol=(\S+))?$
```

**Body.** For every op except `search_replace`, the body is the next fenced block (opening fence with any language tag, closing fence a line that is exactly three backticks). For `search_replace`, the body is the text between `<<<<<<< SEARCH`, `=======` and `>>>>>>> REPLACE`; accept it with or without a surrounding fence.

**Parser leniency (accept, record a warning in `ParsedReply.errors`):** prose before, between or after blocks; `EDIT` in any letter case; `py`, `python` or no language tag; Windows line endings; a path wrapped in quotes or backticks; `symbol=Class.method()` with parentheses; a missing closing fence at the very end of the reply. If no valid `EDIT` block is found, the reply fails with the error "no EDIT blocks found" and the role does one format re-ask (section 9).

**Path rules (strict):** repo-relative, forward slashes, no `..`, not absolute, never inside `.git/` or `.qed/`. The file must exist, except for `create_file`, where it must not exist. Symbol ops require a `.py` file. Protected paths (section 8) are refused for every role except `test_writer`.

**Applier semantics (all use Python's `ast` with `lineno`, `end_lineno`, `col_offset`):**

| Op | What the applier does |
| --- | --- |
| `replace_symbol` | Find the top-level function or class, or a dotted path like `Class.method` (nested classes allowed). Replace from the first decorator line (or the `def`/`class` line) through `end_lineno`. Dedent the new code, re-indent it to the node's `col_offset`, and check it parses on its own and still defines the same name; if the name changed, fail and suggest `insert_after_symbol`. |
| `insert_after_symbol` | Insert the new code after the symbol's last line at the same indentation, with one blank line between members of a class and two at module level. |
| `add_import` | Every line must be an `import` or `from ... import` statement. Skip lines already present. Insert after the last top-level import, or after the module docstring and `__future__` imports when there are none. |
| `search_replace` | The search text must match exactly once. If it matches zero times, retry ignoring trailing whitespace and re-basing indentation, then re-indent the replacement by the same offset. Zero matches: fail and quote the three closest lines (`difflib`). Several matches: fail and ask for more surrounding lines. |
| `create_file` | Create the file and any parent folders. A `.py` file must parse. |

Apply a reply's edits in order on one worktree. If any edit fails, the candidate fails at stage `apply` and nothing from that reply is kept.

**Error text fed back to the repairer** names the edit, the problem and the way out, for example:

```text
EDIT 2 (replace_symbol path=app/auth.py symbol=TokenStore.rotat): symbol not found.
Symbols in this file: TokenStore, TokenStore.issue, TokenStore.rotate, verify_token.
EDIT 4 (search_replace path=app/config.py): SEARCH text not found.
Closest line: "TOKEN_TTL = 60  # seconds". Copy the current code exactly, or use replace_symbol.
```

Track the edit-apply failure rate per model and per op in the run logs; it decides which ops the prompts recommend first.

## 8. Verification cascade, grades and receipts

Every candidate runs the same stages in this order, cheapest first; the first failure rejects it. Each stage returns a `CheckResult`, and every command goes through `verify/run.py` (no shell, timeout, env scrubbing, process-tree kill, log file).

| # | Stage | What runs | Fails when |
| --- | --- | --- | --- |
| 1 | `apply` | The edit applier (section 7) | Any edit cannot be applied |
| 2 | `parse` | `ast.parse` on every changed `.py` file | A syntax error (report file, line and message) |
| 3 | `scope` | Path rules on the diff | A protected path changed (unless allowed), a path is outside the repo or in `.git/` or `.qed/`, a file was deleted, the coder created a test file, or changed lines exceed the speed limit |
| 4 | `risk` | Regex scan of *added* lines in `.py` files | A risky pattern is found and the user does not approve it (non-interactive runs reject unless `--allow-risky`) |
| 5 | `lint` | `ruff check --select E9,F63,F7,F82 --output-format json --no-cache <files>` on the base and candidate versions of changed files | Any rule code appears more often in the candidate than in the base |
| 6 | `fbpa` | The repro test (and any tests added by the diff) with the candidate applied | They do not pass. Skipped when there is no such test. |
| 7 | `targeted` | pytest on the targeted selection | A test fails that passed in the baseline or is new; collection errors fail too; known failures are ignored |
| 8 | `full` | pytest on the whole suite (finalist only, per speed) | Any regression against the baseline |

**Protected paths (default, configurable per project):** `tests/**`, `test/**`, `**/test_*.py`, `**/*_test.py`, `**/conftest.py`, `pytest.ini`, `tox.ini`, `setup.cfg`, `pyproject.toml`, `noxfile.py`, `.github/**`. Config files are protected because they can switch tests off. The guard also rejects added `pytest.skip`, `pytest.mark.skip`, `xfail` and removed `assert` lines in any test file, so weakening a test is caught even when edits are allowed.

**Risky patterns in added lines:** `os.system(`, `subprocess`, `shutil.rmtree(`, `os.remove(`, `os.unlink(`, `os.rmdir(`, `socket`, `requests.`, `urllib`, `httpx.`, `eval(`, `exec(`, `__import__(`, `open(` with an absolute path, `Path.home(`. In `check` mode these are warnings only, because the code is the user's own.

**Targeted test selection, in order:**

1. The repro test file, if any.
2. Test files named after a changed module: `test_<stem>.py` or `<stem>_test.py`.
3. Test files that import a changed module (regex for `import pkg.mod`, `from pkg.mod import`, `from pkg import mod`).
4. If nothing matched and the baseline full suite took 60 seconds or less, run the full suite instead.

**pytest command** (the interpreter comes from the project config):

```text
<python> -m pytest -q -p no:cacheprovider -o junit_family=xunit2 --junitxml <run dir>/report.xml <selection>
```

Parse the JUnit XML; the test key is `<classname>::<name>`. Default timeout is 300 seconds per run (project config). A timeout fails the stage and kills the whole process tree with `psutil`.

**Grade rules (the only place grades are set: `verify/grade.py`):**

```python
def grade(c: Candidate, base: Baseline) -> Grade | None:
    if c.failed_stage is not None:
        return None                                  # rejected
    static_ok = passed(c, "parse") or passed(c, "lint")
    tests_ran = c.tests_passed > 0                   # at least one relevant test ran and passed
    if not static_ok and not tests_ran:
        return Grade.D                               # nothing could be checked
    if not tests_ran:
        return Grade.C                               # checked, not tested
    g = Grade.A if passed(c, "fbpa") and c.full_suite_ran else Grade.B
    if g is Grade.A and (needed_risk_approval(c) or c_has_protected_changes(c)):
        g = Grade.B                                  # proof is weaker: cap at B
    return g
```

In words: **A** = a test failed before and passes now, the full suite shows no regressions, nothing protected changed. **B** = relevant tests pass with no regressions, but no fail-before-pass-after test or no full-suite run. **C** = static checks pass but no relevant test ran. **D** = no check could run (for example only non-Python files changed and no tests exist).

**Receipts.** Write `.qed/receipts/<task id>.json` from the `Receipt` model (section 5). `commands` stores every command needed to re-check, with `{python}` as a placeholder for the interpreter and paths relative to the worktree root. `unverified` lists every gap in plain English, for example:

- "Full suite not run (fast speed)."
- "2 tests were already failing before this change: tests/test\_io.py::test\_big\_file, ..."
- "Tests ran on this computer without network blocking (v1 limitation)."
- "You approved a risky pattern: subprocess in app/run.py."

The one-line summary shown in UIs follows this shape: `Grade A · repro test failed before, passes now · 48/48 tests pass · lint clean · no protected files changed`.

## 9. Prompt templates

Prompts are short and give the model one job with an exact reply format, because small models drift on long instructions. Store each template as a `.txt` file in `src/qedcode/engine/prompts/` and fill `{placeholders}` with `str.format`-style code that escapes braces in user content.

| Role | Temperature | Max output tokens | Retries |
| --- | --- | --- | --- |
| intake | 0.1 | 300 | 1 format re-ask |
| locator | 0.1 | 200 | 1 format re-ask; then fall back to top search hits |
| test\_writer | 0.3 | 1200 | 1 format re-ask; 1 new attempt if the test passes on the base |
| coder | per speed schedule | 2000 (sure: 3000) | 1 format re-ask |
| repairer | 0.3 | 2000 | 1 format re-ask |
| explainer | 0.3 | 250 | none; fall back to a template sentence |

**Context budget.** Prompt budget = model context − max output tokens − 512. Estimate tokens as characters ÷ 3.5 (or use `llama-server`'s `/tokenize` when available). Over budget: drop whole files from the end of the locator's list first, then replace a large file with only its relevant symbols plus an outline of the rest.

**System message (every role):**

```text
You are the coding model inside QEDCode. You do one small job at a time and follow the reply format exactly. Write nothing outside the format. Use only files, functions and APIs shown to you, unless the task asks you to create them.
```

**intake**

```text
TASK:
{request}

PROJECT: Python, tests run with pytest.

Classify the task and list what must be true when it is done.
Reply in exactly this format:
KIND: bugfix | feature | refactor | test | docs | other
ACCEPTANCE:
- <one checkable statement about behaviour>
- <at most 4 lines in total>
QUESTION: <one short question only if the task cannot be understood, otherwise NONE>
```

**locator**

```text
TASK: {request}
ACCEPTANCE:
{acceptance}

FILES AND THEIR TOP-LEVEL SYMBOLS:
{repo_map}

SEARCH HITS FOR WORDS FROM THE TASK:
{hits}

Choose the files someone must read or change to do this task (at most {max_files}).
Reply with one line per file, exactly like this, and nothing else:
FILE: path/to/file.py
```

**test\_writer**

````text
TASK: {request}
ACCEPTANCE:
{acceptance}

RELEVANT CODE:
{code_context}

HOW EXISTING TESTS LOOK ({example_test_path}):
{example_test_snippet}

Write ONE new pytest file with 1 to 3 small tests.
The tests must FAIL on the current code because of the problem in the task, and PASS once the task is done.
Rules: import the code the same way the existing tests do; test only behaviour named in the task;
no network, no subprocess, no files written outside pytest's tmp_path; every test has at least one assert.

Reply in exactly this format:
EDIT create_file path={test_path}
```python
<the whole test file>
```
FAILS NOW BECAUSE: <one line>
````

`{test_path}` is chosen by QEDCode: `tests/test_qed_<task-slug>.py` (or next to existing tests).

**coder** (also used for the helper, with the compact context)

```text
TASK: {request}
ACCEPTANCE:
{acceptance}
{repro_block}
CODE (current contents):
{files_block}

Make the smallest change that completes the task. Do not edit or create test files.
Reply in exactly this format:
PLAN:
- <1 to 3 short lines>
<one or more EDIT blocks>

{protocol_cheatsheet}
```

`{repro_block}` is empty or: `This test must pass after your change:` followed by the test file. `{files_block}` repeats `=== path ===` followed by the file content, without line numbers.

**protocol\_cheatsheet** (inserted into coder and repairer prompts)

````text
EDIT FORMATS (use the simplest one that works):
1) Replace a whole function, method or class (method: symbol=ClassName.method):
EDIT replace_symbol path=pkg/mod.py symbol=func_name
```python
def func_name(...):
    ...the full new definition...
```
2) Add a new function or class after an existing one:
EDIT insert_after_symbol path=pkg/mod.py symbol=existing_name
```python
def new_helper(...):
    ...
```
3) Add import lines:
EDIT add_import path=pkg/mod.py
```python
from datetime import timedelta
```
4) Change a few exact lines (SEARCH must match the current code exactly, once):
EDIT search_replace path=pkg/mod.py
<<<<<<< SEARCH
old lines
=======
new lines
>>>>>>> REPLACE
5) Create a new file:
EDIT create_file path=pkg/new_mod.py
```python
...the whole file...
```
````

**repairer**

````text
TASK: {request}
ACCEPTANCE:
{acceptance}

YOUR LAST CHANGE:
```diff
{diff}
```

WHAT WENT WRONG:
{failure_text}

CURRENT CODE OF THE CHANGED FILES (your change is already applied):
{files_block}

Fix the problem with the smallest change. Edit the CURRENT code above.
Reply in exactly this format:
PLAN:
- <1 to 3 short lines>
<one or more EDIT blocks>

{protocol_cheatsheet}
````

`{failure_text}` comes from the failed `CheckResult.details`: the edit errors, the syntax error, or the first failing test with its assertion and a traceback trimmed to 40 lines.

**explainer**

````text
Explain this code change to a beginner in 2 to 4 short sentences:
what was wrong, what changed, and why it works. No code, no lists.

TASK: {request}
CHANGE:
```diff
{diff}
```
CHECKS: {checks_summary}
````

**Format re-ask** (sent once, as a new user message after the bad reply):

```text
Your reply did not follow the required format: {problem}. Reply again using exactly the format from the instructions. Write nothing else.
```

## 10. Runtime: hardware, models and lifecycle

The engine sees only `ModelClient`; everything about GPUs, servers and downloads stays in `runtime/`, `hardware/` and `runtime/installer.py`.

```python
class ModelClient(Protocol):
    name: str                      # "llamacpp:qwen3.5-4b-q4km", "cloud:openrouter/..."
    context_tokens: int
    def chat(self, messages: list[dict], *, temperature: float, max_tokens: int,
             stop: list[str] | None = None,
             on_token: Callable[[str], None] | None = None,
             tag: str = "") -> str: ...   # tag = role name, for logs and FakeModel
    def health(self) -> bool: ...
    def unload(self) -> None: ...
```

**Backends:**

| Backend | How QEDCode talks to it | Lifecycle |
| --- | --- | --- |
| `llamacpp` | Starts `llama-server`, then `POST /v1/chat/completions` (OpenAI format, streaming) | QEDCode owns the process (server manager below) |
| `ollama` | `POST http://127.0.0.1:11434/api/chat` with `options.num_ctx`, `options.temperature` and `keep_alive`; models via `GET /api/tags`; downloads via `POST /api/pull` (streams progress) | Unload with a request carrying `keep_alive: 0`; Ollama swaps models itself |
| `openai_compat` | Any `base_url`, e.g. LM Studio at `http://127.0.0.1:1234/v1` | None; the user runs the server |
| `cloud` | `litellm.completion(model=..., api_key=...)` with the key from `keyring` | None; record token counts in receipts |
| `fake` | Scripted replies per role (tests only) | None |

**llama-server launch.** Run `llama-server --help` once per installed build and cache which flags exist, because flag spellings change between releases.

```text
llama-server -m <model.gguf> --host 127.0.0.1 --port <free port> -c <ctx> -ngl 99 --parallel 1 --jinja
             [--flash-attn on]  [-ctk q8_0 -ctv q8_0]          # KV types only when flash attention is on
```

CPU-only uses `-ngl 0`. Poll `GET /health` every 500 ms until it returns 200 (timeout 120 s) and show "Loading model..." meanwhile. Capture stderr to `~/.qedcode/logs/llama-server.log`; on an out-of-memory failure, suggest a smaller context or model.

**Server manager (one shared local server per machine).** The TUI, CLI and MCP server must not start three copies of a model on a 4–6 GB card.

- State file `~/.qedcode/run/server.json`: pid, port, model id, backend, started\_at, last\_used. Guard starts with a lock file.
- Every client first reuses a live server with the same model; a different model means stop the QEDCode-owned server, then start the new one.
- Each client registers in `~/.qedcode/run/clients/<pid>` and updates `last_used` on every call.
- Unload when idle longer than `keep_alive_minutes` (default 10; long-running clients check every 30 s), and on exit when `unload_on_exit = true` and no other client is registered.

**Hardware detection (`hardware/detect.py`)** returns vendor, GPU name, usable VRAM in GB, suggested backend, driver version and how it was detected. Always show the result and let the user override every field.

| Hardware | Detect with | VRAM from |
| --- | --- | --- |
| NVIDIA | `nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader,nounits`; the plain `nvidia-smi` header gives the highest supported CUDA version | `memory.total` (MiB) |
| AMD on Linux | `/sys/class/drm/card*/device/vendor` equals `0x1002` | `mem_info_vram_total` in the same folder (bytes) |
| AMD or Intel on Windows | PowerShell `Get-CimInstance Win32_VideoController` for names | Registry value `HardwareInformation.qwMemorySize` under the display adapter class key. Never trust `AdapterRAM`; it caps at 4 GB. |
| Intel on Linux | vendor `0x8086` | Integrated GPUs share system RAM: treat as CPU-class unless the user says it is a discrete Arc card |
| Apple Silicon | `platform.system() == "Darwin"` and `platform.machine() == "arm64"`; `sysctl -n hw.memsize` | Usable = 60% of RAM (unified memory) |
| None found | Fallback | CPU-only; warn that it will be slow |

**Which llama.cpp build to install.** Read `https://api.github.com/repos/ggml-org/llama.cpp/releases/latest` (or the pinned tag in config) and match asset names by substring. Archives are `.zip` on Windows and may be `.tar.gz` elsewhere; support both. Upstream has not shipped a Linux CUDA build (check the current release), so Linux NVIDIA users get Vulkan, or Ollama. Download only from the official `ggml-org/llama.cpp` releases, never from forks, and check the current asset names there before writing the matcher, because names change between releases.

| Hardware | Windows | Linux | macOS |
| --- | --- | --- | --- |
| NVIDIA | `win-cuda-<ver>-x64` plus the matching `cudart-llama-bin-win-cuda-<ver>-x64` DLLs; choose the newest CUDA version the driver supports (RTX 50-series needs a CUDA 12.8+ build); fallback `win-vulkan-x64` | `ubuntu-vulkan-x64` | — |
| AMD | `win-vulkan-x64` (default) or `win-hip-radeon-x64` | `ubuntu-vulkan-x64` (default) or `ubuntu-rocm-<ver>-x64` | — |
| Intel | `win-vulkan-x64` | `ubuntu-vulkan-x64` | — |
| Apple Silicon | — | — | `macos-arm64` (Metal). If it will not start on an older macOS, suggest `brew install llama.cpp` or Ollama. |
| CPU-only | `win-cpu-x64` | `ubuntu-x64` | `macos-arm64` or `macos-x64` |

Extract to `~/.qedcode/bin/llama.cpp/<tag>/<backend>/`, then run `llama-server --version`. If that fails, try the next backend (CUDA, then Vulkan, then CPU) and tell the user why. Pin a release tag that CI has tested; `qed doctor --update-engine` moves to the latest after asking.

**Model catalog (`runtime/catalog.yaml`).** Every entry must be verified on Hugging Face before release: repo, filename, size and SHA-256. KV numbers come from the model's `config.json`.

```yaml
- id: qwen3.5-4b-q4km
  display: Qwen3.5 4B (Q4_K_M)
  gguf: {repo: "<verify on HF>", file: "<verify>.gguf", size_gb: "<verify>", sha256: "<fill>"}
  ollama_tag: qwen3.5:4b
  license: Apache-2.0
  kv_bytes_per_token_f16: 32768   # 8 full-attention layers x 2 x 4 KV heads x 256 dim x 2 bytes
  state_mb_per_seq: 50            # hybrid (Gated DeltaNet) layers keep a fixed-size state
  rank: 1                         # lower = preferred when it fits
- id: qwen2.5-coder-7b-q4km
  display: Qwen2.5-Coder 7B (Q4_K_M)
  gguf: {repo: "<verify on HF>", file: "<verify>.gguf", size_gb: 4.68, sha256: "<fill>"}
  license: Apache-2.0
  kv_bytes_per_token_f16: 57344   # 28 layers x 2 x 4 KV heads x 128 dim x 2 bytes
  state_mb_per_seq: 0
  rank: 2
```

**Recommendation (`hardware/recommend.py`):**

```text
need_gb = weights_gb + kv_gb + state_gb + 0.6 (compute buffers) + reserve_gb
kv_gb   = kv_bytes_per_token_f16 x ctx x (0.53 if KV is q8_0 else 1.0) / 2^30
reserve_gb = 0.4 when a desktop runs on the same GPU, else 0
```

Pick the best-ranked model whose `need_gb` at an 8192-token context fits the usable VRAM, then the largest context from 8192, 12288, 16384, 24576, 32768 that still fits. CPU-only: the smallest model, context 8192, and a speed warning.

**Downloads (always ask first, show the size).** GGUF files: `huggingface_hub.hf_hub_download(repo_id, filename, local_dir=~/.qedcode/models/<id>/)`, then check SHA-256 when the catalog lists it. Ollama models: `POST /api/pull` with streamed progress. Never download anything in tests.

**Telemetry for the UI header:** NVIDIA via `nvidia-ml-py`, AMD on Linux via sysfs `mem_info_vram_used`, otherwise show "n/a".

## 11. Interfaces

All three front doors are thin: they collect input, show `Event`s, answer `ask`/`confirm`, and call `engine.api`. Exit codes: 0 success, 1 task finished without a fix, 2 usage or setup error, 3 internal error.

**CLI (`cli.py`, Typer).** The console script is `qed`; the package name is `qedcode`.

| Command | Does |
| --- | --- |
| `qed` | Opens the TUI in the current folder (runs global setup first if needed) |
| `qed setup [--reset]` | Global wizard: hardware, mode, model, downloads, helper, API keys, agent connection |
| `qed solve "<task>" [--speed fast\|normal\|sure] [--apply] [--json] [--allow-risky]` | Non-interactive solve; prints progress with Rich, or one JSON `SolveResult` |
| `qed check [--speed ...] [--base <ref>] [--json]` | Grades the current uncommitted changes |
| `qed verify <receipt.json>` | Re-runs a receipt; prints VERIFIED or the mismatches |
| `qed apply <task id>` / `qed undo` | Applies a finished result / reverts the last apply |
| `qed models [list\|recommend\|download <id>\|use <id>\|remove <id>]` | Model management |
| `qed doctor [--update-engine]` | Checks git, Python, pytest, ruff, hardware, engine build, model server; prints fixes |
| `qed mcp` | Runs the MCP server over stdio |
| `qed uninstall [--all]` | Removes the project's `.qed/`; with `--all`, also `~/.qedcode` (asks first) |

**First run in a project** (TUI or CLI): if the folder is not a git repo, offer `git init` plus a first commit, or stop. Detect the Python interpreter, pytest and the protected paths, show them, and ask the user to confirm once; save to `.qed/project.toml`. Offer to add `.qed/` to `.gitignore`.

**TUI (`tui/`, Textual).**

```text
+-- QEDCode -- <project> --------------------------------------------------+
| <model> . <backend> . <GPU, VRAM used/total>        [ready|loading|busy]  |
| Speed: Fast [Normal] Sure        Mode: local         Tests: pytest (48)   |
+--------------------------------------------------------------------------+
|  log: one line per Event, newest at the bottom, auto-scroll               |
|  candidate cards: c1 failed (targeted) | c2 B | c3 A                      |
|  result panel: grade, one-line summary, explanation                       |
+--------------------------------------------------------------------------+
| > task input (Enter to run; /commands available)                          |
| a apply  d diff  e explain  h try harder  u undo  r receipt  m speed  q   |
+--------------------------------------------------------------------------+
```

| Key or command | Action |
| --- | --- |
| Enter | Run the task in the input box (engine runs in a Textual worker thread) |
| `a` | Apply the result to the user's files (confirm if grade is C or D) |
| `d` | Full-screen diff with syntax highlighting |
| `e` | Show the explanation |
| `h` | Try harder: rerun at the next speed, starting from the best candidate |
| `u` | Undo the last apply |
| `r` | Show the receipt path and summary |
| `m` | Cycle speed: fast, normal, sure |
| `s` | Settings screen (mode, model, keep-alive, helper) |
| Esc | Cancel the running task |
| `?` | Help screen |
| `q` | Quit (unloads the model if no other client uses it) |
| `/check`, `/verify <file>`, `/models`, `/speed <name>`, `/help` | Same as the CLI commands, inside the TUI |

Dialogs: risky-pattern approval (shows the lines), helper approval (shows that code leaves the machine in cloud helpers), apply confirmation for grade C/D, intake question. While the model loads, show a progress line and keep the input usable.

**MCP server (`mcp_server.py`, official SDK `FastMCP`, stdio).** Load the model lazily on the first call; one job per project at a time (lock file `.qed/lock`); a second call returns `status: "busy"`.

| Tool | Arguments | Returns |
| --- | --- | --- |
| `solve` | `task: str`, `speed: str = "normal"`, `apply: bool = false`, `wait_seconds: int = 600`, `project_dir: str = cwd` | `status` (done, running, no\_fix, needs\_input, busy), `job_id`, `grade`, `summary`, `explanation`, `diff`, `receipt_path`, `question` |
| `check` | `speed: str = "normal"`, `base: str = "HEAD"`, `project_dir` | `grade`, `summary`, `failing_tests`, `receipt_path` |
| `verify` | `receipt_path: str` | `verified: bool`, `mismatches` |
| `status` | `job_id: str` | Progress lines so far, or the final result |

`solve` runs in a background thread. It waits up to `wait_seconds` (reporting progress through the SDK's progress notifications when the client supports them), then returns either the result or `status: "running"` with a `job_id` that `status` can poll. Non-interactive rules apply: `ask` returns None, `confirm` returns False, so risky patterns are rejected and the helper is never used unless the config pre-approves it.

**Agent connection.** `qed setup` offers to run `claude mcp add qedcode -- qed mcp` when the `claude` command exists, and prints ready-to-paste snippets for OpenCode, Cline and Cursor (their config formats change; link their docs instead of hardcoding paths).

## 12. Config and file locations

Two TOML files: one global, one per project. Both load into Pydantic models (`GlobalConfig`, `ProjectConfig`) with defaults, so a missing key never crashes. Tests set `QEDCODE_HOME` to a temp folder.

**Global: `~/.qedcode/config.toml`**

```toml
[hardware]                 # detected by qed setup; the user may edit
vendor = "nvidia"          # nvidia | amd | intel | apple | cpu
gpu_name = "NVIDIA GeForce RTX 4050 Laptop GPU"
vram_gb = 6.0              # usable VRAM QEDCode plans for
backend = "cuda"           # cuda | vulkan | rocm | hip | metal | cpu

[engine]
mode = "local"             # local | mix | cloud
speed = "normal"           # fast | normal | sure
keep_alive_minutes = 10
unload_on_exit = true
learn = true             # remember applied A/B fixes, show similar ones to the local model

[local]
backend = "llamacpp"       # llamacpp | ollama | openai_compat
model = "qwen3.5-4b-q4km"  # catalog id, Ollama tag, or a path to a .gguf
base_url = ""              # openai_compat only, e.g. http://127.0.0.1:1234/v1
ctx = 16384
kv_cache = "q8_0"          # f16 | q8_0
llamacpp_tag = "<pinned release tag>"

[helper]                   # mix mode only
enabled = false
backend = "cloud"          # cloud | ollama | openai_compat
model = ""                 # e.g. a LiteLLM model string
ask_before_use = true      # MCP and CLI runs never use the helper unless this is false

[cloud]                    # cloud mode
model = ""                 # LiteLLM model string
# API keys are never stored here: keyring service "qedcode", or the provider's env var.
```

**Per project: `<repo>/.qed/project.toml`** (written after the user confirms the detected values)

```toml
language = "python"
python = ".venv/bin/python"       # the interpreter that runs this project's tests
test_command = ["{python}", "-m", "pytest"]
test_timeout_seconds = 300
protected = ["tests/**", "test/**", "**/test_*.py", "**/*_test.py", "**/conftest.py",
             "pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml", "noxfile.py", ".github/**"]
confirmed = true
```

**Python interpreter detection order:** `.venv/` then `venv/` in the repo (`Scripts/python.exe` on Windows, `bin/python` elsewhere); an active `VIRTUAL_ENV`; `uv run python` when `uv.lock` exists; `python3`, then `python` on PATH. Show the choice and let the user change it. If pytest is missing in that interpreter, offer to install it there (ask first).

**Folders:**

| Path | Holds |
| --- | --- |
| `~/.qedcode/config.toml` | Global config |
| `~/.qedcode/bin/` | Downloaded llama.cpp builds |
| `~/.qedcode/models/` | Downloaded GGUF models |
| `~/.qedcode/run/` | Server state file, locks, client registrations |
| `~/.qedcode/logs/` | App log, llama-server log (rotate at 5 MB, keep 3) |
| `<repo>/.qed/project.toml` | Project config |
| `<repo>/.qed/worktrees/` | Temporary worktrees, removed after each task (`git worktree prune` on start) |
| `<repo>/.qed/runs/<task id>/` | Prompts, replies, check logs, JUnit reports for that task |
| `<repo>/.qed/receipts/<task id>.json` | Receipts |
| `<repo>/.qed/last_apply.json` | What the last apply changed, for undo |

**Snapshot recipe (`vcs/snapshot.py`)** — captures tracked and untracked (non-ignored) files without touching the user's index or working tree:

```text
GIT_INDEX_FILE=<repo>/.qed/tmp/index   (temporary index, set only for these commands)
  git read-tree HEAD                     # skip on a repo with no commits
  git add -A
  tree   = git write-tree
  commit = git commit-tree <tree> -p HEAD -m "qed snapshot <task id>"
git update-ref refs/qed/snapshots/<task id> <commit>
git worktree add --detach <repo>/.qed/worktrees/<task id>-<cand id> <commit>
```

Before the first snapshot, add `.qed/` to `.git/info/exclude` (local to the clone, never committed), so QEDCode's own files never enter a snapshot even when the user declines the `.gitignore` change.

**Apply and undo (`vcs/patch.py`):** the patch is `git diff --binary <snapshot>..<candidate commit>`. Before applying, compare each touched file in the user's tree with its snapshot version (`git hash-object` against `git rev-parse <snapshot>:<path>`). All equal: `git apply --binary` in the user's tree (working tree only, never the index) and save the patch to `last_apply.json`. Any difference: refuse and name the files that changed. Undo runs `git apply -R` with the same equality check against the applied state.

## 13. Repository layout, coding standards and testing

The layout mirrors the module map in section 4; create folders only when a step needs them.

```text
qedcode/
  pyproject.toml            # hatchling; console script: qed = qedcode.cli:app
  README.md  AGENTS.md  CLAUDE.md  PROGRESS.md  LICENSE
  src/qedcode/
    __init__.py             # __version__
    cli.py                  # Typer app
    config.py               # GlobalConfig, ProjectConfig, load/save, QEDCODE_HOME
    models/schemas.py       # section 5
    hardware/detect.py  hardware/recommend.py
    runtime/client.py       # ModelClient protocol + factory from config
    runtime/llamacpp.py  runtime/server_manager.py  runtime/ollama.py
    runtime/openai_compat.py  runtime/cloud.py  runtime/fake.py
    runtime/installer.py  runtime/catalog.yaml  runtime/catalog.py
    project/detect.py  project/repo_map.py  project/search.py  project/context.py
    vcs/git.py  vcs/snapshot.py  vcs/worktree.py  vcs/patch.py
    edits/protocol.py  edits/apply.py
    verify/run.py  verify/pytest_runner.py  verify/lint.py  verify/guard.py
    verify/cascade.py  verify/baseline.py  verify/grade.py  verify/receipt.py  verify/verify.py
    engine/api.py  engine/solve.py  engine/check.py  engine/roles.py
    engine/select.py  engine/budget.py  engine/prompts/*.txt
    tui/app.py  tui/screens.py  tui/widgets.py  tui/app.tcss
    mcp_server.py  setup_wizard.py  logging_setup.py
  tests/unit/  tests/integration/  tests/fixtures/
  bugzoo/<NNN_name>/        # see below
  .github/workflows/ci.yml
```

**Coding standards:**

- Type hints everywhere; `ruff check` and `ruff format` clean; functions under about 60 lines.
- One subprocess helper (`verify/run.py`, used by `vcs/git.py` too): list arguments, `cwd`, timeout, scrubbed env, captured output, log file, process-tree kill.
- No global mutable state; pass config objects explicitly.
- Pure functions where possible (parsing, grading, selection, recommendation) so they are easy to test.
- Logging through the standard `logging` module to `~/.qedcode/logs/qedcode.log`; never `print` from engine code.
- Docstrings on public functions: one line on what, one on why if not obvious.
- Windows: handle `\r\n`, long paths and `Scripts\python.exe`; never assume `/tmp`.

**Testing strategy:**

| Layer | How |
| --- | --- |
| Unit | Every module. Parser: messy replies (extra prose, missing fences, wrong case). Applier: decorators, methods, nested classes, indentation, CRLF. Grade: every rule. Recommendation: VRAM edge cases. |
| Git | Temporary repos in `tmp_path`: dirty tree, untracked files, no commits, spaces in paths. Snapshot never changes `git status` of the user repo. |
| Engine | `FakeModel` replays scripted replies per role from `tests/fixtures/scripts/*.yaml`, and records the prompts it received so tests can assert on them. |
| End to end | For each bug zoo project: a scripted good fix reaches grade A; a scripted broken fix is rejected, then repaired; a test-editing fix is rejected at `scope`. |
| Real model | Marked `@pytest.mark.gpu`, skipped in CI, run by hand: solve 3 bug zoo tasks with the configured local model. |
| CI | GitHub Actions on ubuntu, windows and macos with Python 3.11 and 3.12: `ruff check`, `ruff format --check`, `pytest -m "not gpu"`. |

**Bug zoo format.** Each `bugzoo/<NNN_name>/` folder is a tiny standalone Python project:

```text
bugzoo/001_paginate_off_by_one/
  task.txt              # the user request, one or two sentences
  src/shop/pages.py     # the buggy code
  tests/test_pages.py   # visible tests: pass on the buggy code, do not catch the bug
  hidden/test_hidden.py # fails on the buggy code, passes on the fix; never shown to QEDCode
  solution.patch        # a known-good fix
  edits.txt             # the same fix written in the edit protocol, for FakeModel scripts
```

`bugzoo/check_zoo.py` proves each project is valid: visible tests pass on the buggy code, hidden tests fail on it, and after applying `solution.patch` both pass.

## 14. Safety rules and definition of done

QEDCode runs code that a model wrote on the user's computer, so safety is part of every feature, not a later phase.

| Threat | v1 control |
| --- | --- |
| Model-written code or tests damage files | Worktree isolation; risky-pattern guard on added lines; process timeouts; nothing touches the user's tree before apply |
| Prompt injection from repo content (README, comments, issue text) | Repo text is data: prompts label it as code to edit, the model cannot choose commands, and the command allowlist is fixed in code |
| Tests switched off to fake a pass | Protected paths, skip/xfail/assert guard, hidden tests in the bug zoo, FBPA required for grade A |
| Secrets leaking | Keys only in keyring or env vars; env scrubbing for project commands; never logged, never in prompts or receipts |
| Code leaving the machine | Local mode sends nothing out; mix and cloud mode tell the user before code goes to a remote model; MCP runs never call the helper unless pre-approved |
| Broken install or runaway downloads | Ask before every download with sizes; checksums when listed; everything under `QEDCODE_HOME`; `qed uninstall --all` |
| Lost user work | Apply refuses when files changed since the snapshot; undo with the same check; worktrees cleaned up |

**Known v1 limit, stated to users:** tests run on the host without network blocking or a container. The `unverified` list in every receipt says so. The sealed test box (Docker, network off) is planned for v1.1.

**Definition of done for every step:**

- The feature works on the bug zoo with `FakeModel`, and the step's "Done when" checks pass by hand.
- New code has unit tests; `uv run pytest -m "not gpu"` and `uv run ruff check .` pass.
- No engine module imports a UI module (CI check: `grep` for forbidden imports).
- User-facing messages are plain English with a next step.
- No new dependency outside the allowed stack.
- `PROGRESS.md` updated: what was done, what is next, known issues.
- The change is one focused commit with a message like `M3.4 ast applier: replace_symbol and insert_after_symbol`.

## 15. After v1: future features

Each feature below is built in the order of the build plan's "After v1" table. A feature gets its section here before any code. Every feature keeps the rules of section 2: engine purity, no model-run commands, `FakeModel` tests, ask before every download.

### 15.1 Learns your project (v1.1)

**What it does.** When the user applies a proven fix (grade A or B), QEDCode remembers it. On the next task in the same project it finds the most similar earlier fixes and shows them to the local model as examples ("this is how this project solved a similar thing"). Everything stays on the user's computer.

**Where it lives.** `.qed/memory.db`, a SQLite file with an FTS5 table. `.qed/` is already excluded from git and removed by `qed uninstall`.

**Modules touched.**

| Module | Change |
| --- | --- |
| `project/memory.py` (new) | `Memory` class: `record(receipt)`, `similar(request, limit)`, `list()`, `forget(id)`, `clear()`; `render_examples(lessons, max_tokens)` |
| `engine/api.py` | `apply_result` records the lesson after a successful apply; `memory_list`, `memory_forget`, `memory_clear` |
| `engine/solve.py` | fetches examples once per run (local model only) and passes them to the coder |
| `engine/roles.py`, `engine/prompts/coder.txt` | the coder prompt gets an `{examples_block}` slot, empty when there are no examples |
| `config.py` | `[engine] learn = true` switches the feature off |
| `cli.py` | `qed memory [list\|forget <id>\|clear]` |

**Rules.**

- A lesson is saved only from `apply_result`, after the user's files were changed and only for grade A or B. Candidates the user never applied are never remembered.
- A lesson stores: task id, request, kind, grade, changed file paths, the diff (cut at 4000 characters) and the time. No file contents, no model replies, no keys.
- `similar()` ranks with FTS5 `bm25` over request, file paths and diff; the query is built from the request's keywords (letters and digits only, so FTS syntax cannot be injected). Lessons whose changed files no longer exist in the project are skipped. At most 2 lessons are used.
- The examples block is cut to at most 15% of the model's context and at most 1200 tokens; with a context under 6000 tokens no examples are used. It is labelled as data: "Earlier fixes in this project, for style only".
- Examples go only to the local model. They are never sent to the helper or a cloud model (cloud mode and the helper get none).
- The newest 200 lessons are kept; older ones are deleted.
- A broken or unreadable `memory.db` is never fatal: it is ignored, a warning is logged, and `qed memory clear` recreates it.

**v1.1 limits.** Keyword matching only (no embeddings); one memory per project; lessons are not shared between projects or people.
