# QEDCode — Build Plan (Step by Step)

Nov 20, 2026 · @Susamanth Nellore

## Before you start

Install these tools and spend about an hour on setup before the first prompt. You do this once.

| Tool | Why | Check it works |
| --- | --- | --- |
| Python 3.12 | QEDCode is written in Python | `python --version` |
| uv | Creates the project, installs packages, runs tests | `uv --version` |
| Git | QEDCode depends on git, and you commit after every step | `git --version` |
| An AI coding agent | Writes the code with you: Claude Code, Cursor, OpenCode or similar | It opens your repo folder |
| A GitHub account | Hosts the repo and runs the tests (CI) on Windows, macOS and Linux | You can create a repo |
| Your GPU driver, up to date | Needed only at the real-model checks | `nvidia-smi` shows your RTX 5060 Ti |

Ollama is optional. It is handy for quick real-model tests before your own llama.cpp setup works.

**Set up the repo:**

1. Check that the name `qedcode` is free on GitHub and on PyPI (`pypi.org/project/qedcode`). If it is taken, pick a variant now; renaming later is painful.
2. Create an empty GitHub repo called `qedcode` and clone it.
3. Export the doc "QEDCode — AI Build Brief" as Markdown and save it in the repo root as `AGENTS.md`.
4. Create `CLAUDE.md` with one line: `Read AGENTS.md before doing anything and follow its rules.` Claude Code reads `CLAUDE.md`; most other agents read `AGENTS.md`.
5. Create `PROGRESS.md` from the template below.
6. Commit: `git add . && git commit -m "Add build brief"` and push.

**PROGRESS.md template:**

```markdown
# QEDCode progress

## Current step
M0.1

## Done
- (step, date, one line)

## Known issues
- 

## Values I must verify by hand
- 

## Real-model results
- 
```

**Start every AI session with this message**, then paste the step's prompt:

```text
Read AGENTS.md and PROGRESS.md. We are doing step <M.N> from the build plan. Do only this step.
If anything in the step conflicts with AGENTS.md, stop and ask me. Do not edit AGENTS.md.
```

## How to vibe code this safely

Build one small step at a time, check it yourself, and commit only what works. The AI writes the code; you are the one who decides it is done.

**Rules:**

1. **One step per AI session.** Never ask for "build QEDCode". Big asks produce big messes.
2. **Paste the step's prompt as it is.** Add your own notes under it, not instead of it.
3. **Check "Done when" yourself.** Run the commands. "It works" from the AI is not proof (that is the whole idea of QEDCode).
4. **Green means commit; red means paste the exact error back.** After three failed fixes, run `git restore .` (or `git reset --hard`) and redo the step with a smaller ask.
5. **Before every commit** run `uv run pytest -m "not gpu"` and `uv run ruff check .`.
6. **AGENTS.md is the boss.** If the AI wants to change a rule, it asks you, and you edit the file yourself.
7. **Read the diff.** If the AI touched files outside the step, ask why or revert them.
8. **Use your strongest model for M3, M4 and M5** (edit applier, checks, solve loop). UI steps work fine with cheaper models.
9. **Build without the GPU first.** The fake model and the bug zoo make every step testable on any computer. Use your real GPU only at the steps marked "Real-model check".
10. **Write things down.** Save prompts that worked and results in `PROGRESS.md`.

**Every step below has the same four parts:**

- **Goal**: what the step adds, in one line.
- **Prompt**: paste it into your AI agent after the session-start message.
- **Done when**: tick each box only after you checked it yourself.
- **Watch out**: the mistake AI agents most often make in that step.

Rough time assumes 1–2 hours a day; it is a guess, so expect some steps to be faster and some slower.

&#91;embedded content: build roadmap · 9 milestones, 3 need your GPU\]

Milestones run down the left column, then the right. Only the three marked "GPU check" need your graphics card; everything else runs on the fake model.

## Milestone 0: Project skeleton (about 2 days)

At the end of M0 you have an installable `qed` command, settings files and tests running on three operating systems.

### Step 0.1 — Create the package

**Goal:** an empty but real Python project with a `qed` command and CI.

**Prompt:**

```text
Read AGENTS.md sections 1, 2, 11 and 13. Do step 0.1: create the project skeleton.
- Use uv and hatchling, src layout, package name "qedcode", Python >= 3.11, version 0.0.1.
- Console script "qed" -> qedcode.cli:app (Typer). For now `qed --version` prints the version and
  `qed` with no arguments prints "QEDCode is not ready yet".
- Dev dependencies: pytest, pytest-asyncio, ruff. Configure ruff (line length 100) and pytest
  (testpaths = tests, register the marker "gpu").
- Create the folders from AGENTS.md section 13 with __init__.py files only. No engine code yet.
- tests/unit/test_cli.py: test `qed --version` with Typer's CliRunner.
- .github/workflows/ci.yml: ubuntu, windows and macos x Python 3.11 and 3.12; run
  `ruff check`, `ruff format --check`, `pytest -m "not gpu"`.
- README.md: one paragraph about QEDCode and how to run the tests.
Update PROGRESS.md when done.
```

**Done when:**

- [ ] `uv run qed --version` prints `0.0.1`
- [ ] `uv run pytest -m "not gpu"` and `uv run ruff check .` pass
- [ ] CI on GitHub is green on Windows, macOS and Linux

**Watch out:** agents like to add extra files, frameworks or dependencies. Delete anything you did not ask for.

### Step 0.2 — Data models and settings

**Goal:** the shared data types and the two config files every later step uses.

**Prompt:**

```text
Read AGENTS.md sections 5 and 12. Do step 0.2.
- src/qedcode/models/schemas.py: exactly the models from section 5, plus Grade.rank().
- src/qedcode/config.py: GlobalConfig and ProjectConfig as Pydantic models matching the TOML
  in section 12, with defaults; load_global(), save_global(), load_project(root),
  save_project(root, cfg). QEDCODE_HOME overrides ~/.qedcode. Create folders on first save.
- API keys are never stored in config.
- Tests: save/load round trip for both configs in tmp_path; a missing file gives defaults;
  unknown keys are ignored with a logged warning; Grade.rank() ordering.
Update PROGRESS.md.
```

**Done when:**

- [ ] Tests pass, and test runs create config files only in temp folders, never in your real home folder
- [ ] You skimmed `schemas.py` and it matches section 5 of the brief

**Watch out:** agents sometimes "improve" the schemas with extra fields. Extra fields now become confusion later; remove them.

## Milestone 1: Bug zoo and fake model (about 3 days)

At the end of M1 you can test the whole engine on any computer, with no GPU: 10 tiny buggy projects plus a fake model that gives scripted answers.

### Step 1.1 — Build the bug zoo

**Goal:** 10 tiny Python projects, each with a known bug, a hidden test that catches it, and a known fix.

| # | Folder | The problem |
| --- | --- | --- |
| 001 | `paginate_off_by_one` | `paginate(items, page, size)` drops the last item of each page |
| 002 | `discount_threshold` | A discount applies above 100 instead of at 100 or more |
| 003 | `mutable_default` | `add_tag(tag, tags=[])` shares one list between calls |
| 004 | `empty_input_crash` | `parse_csv_line("")` crashes instead of returning `[]` |
| 005 | `integer_average` | `average([1, 2])` returns 1 instead of 1.5 |
| 006 | `missing_config_key` | `load_config()` crashes when "timeout" is missing; it should default to 30 |
| 007 | `sort_order` | `top_scores()` returns the lowest scores first |
| 008 | `slugify_feature` | New feature: `slugify(text)` (lowercase, spaces to dashes, drop punctuation) |
| 009 | `factorial_zero` | `factorial(0)` returns 0 instead of 1 |
| 010 | `cart_quantity` | `Cart.total()` ignores item quantity (a method inside a class) |

**Prompt:**

```text
Read AGENTS.md section 13 ("Bug zoo format"). Do step 1.1: create bugzoo/ with these 10 projects:
<paste the table from the build plan>
Each project: task.txt (one or two plain sentences, written like a real user), buggy code in
src/<pkg>/, visible tests in tests/ that PASS on the buggy code and do not catch the bug,
hidden/test_hidden.py that FAILS on the buggy code and PASSES after the fix, solution.patch
(git diff format), and edits.txt (the same fix written in the edit protocol, AGENTS.md section 7).
Each project must run with `python -m pytest` from its own folder (add a small pyproject.toml
or conftest.py). Keep each project under 60 lines of code.
Write bugzoo/check_zoo.py: for each project, in a temp copy, check visible tests pass and hidden
tests fail, then apply solution.patch and check both pass. Print a table; exit 1 if any is invalid.
```

**Done when:**

- [ ] `uv run python bugzoo/check_zoo.py` reports 10 valid projects
- [ ] You read three `task.txt` files and they sound like a real person asking for help

**Watch out:** visible tests that already catch the bug make the zoo useless. `check_zoo.py` must prove they pass on the buggy code.

### Step 1.2 — The fake model

**Goal:** a scripted stand-in for the AI model, so engine tests are fast, free and repeatable.

**Prompt:**

```text
Read AGENTS.md sections 9, 10 and 13. Do step 1.2.
- runtime/client.py: the ModelClient protocol from section 10 (chat has a `tag` argument = role name).
- runtime/fake.py: FakeModel(script) where script maps a tag ("intake", "locator", "test_writer",
  "coder", "repairer", "explainer") to a list of replies returned in order. It records every call
  (messages, temperature, max_tokens, tag) for assertions, and raises a clear error when a list
  runs out or a tag has no script.
- Load scripts from YAML files in tests/fixtures/scripts/.
- Write the script for bug zoo 001: a good reply for every role, using its edits.txt for the coder.
- Unit tests for FakeModel.
```

**Done when:**

- [ ] FakeModel tests pass
- [ ] `tests/fixtures/scripts/001_paginate_off_by_one.yaml` exists with replies for all six roles

**Watch out:** do not let the agent make FakeModel "smart". It should only replay what the script says.

## Milestone 2: Hardware, models and setup (about 6 days)

At the end of M2, `qed setup` finds your GPU, downloads the right engine and model after asking, and gets a first answer from a real local model.

### Step 2.1 — Hardware detection

**Goal:** know the GPU vendor and VRAM on any machine, never crash, always allow an override.

```text
Read AGENTS.md section 10 ("Hardware detection"). Do step 2.1.
- hardware/detect.py: detect() -> HardwareInfo (vendor, gpu_name, vram_gb, backend, driver,
  cuda_version, source, notes) for NVIDIA, AMD (Linux sysfs, Windows registry), Intel, Apple
  Silicon and CPU fallback, exactly as the table says. Every probe has a short timeout; on any
  error it falls through to the next probe and adds a note.
- `qed doctor` prints a Rich table of what was found and how.
- Unit tests mock command output and files for every vendor path. No real hardware in tests.
```

**Done when:**

- [ ] `uv run qed doctor` shows your RTX 5060 Ti with the right VRAM (8 or 16 GB)
- [ ] Tests cover NVIDIA, AMD, Intel, Apple and CPU-only

### Step 2.2 — Model catalog and recommendation

**Goal:** pick the best model and context size that fit the user's card.

```text
Read AGENTS.md section 10 ("Model catalog", "Recommendation"). Do step 2.2.
- runtime/catalog.yaml: Qwen3.5-4B, Qwen3.5-2B, Qwen3.5-9B and Qwen2.5-Coder-7B as Q4_K_M GGUF.
  Look up the real Hugging Face repo, filename, size and SHA-256, and the KV numbers from each
  config.json. If you cannot verify a value, write "<verify>" and list it in PROGRESS.md.
- runtime/catalog.py: load and validate entries with Pydantic.
- hardware/recommend.py: recommend(hw, catalog) -> model id, ctx, kv_cache, need_gb, reason,
  using the formula in section 10.
- `qed models list` and `qed models recommend`.
- Tests: 4 GB, 6 GB, 8 GB, 16 GB, Apple with 16 GB RAM, CPU-only.
```

**Done when:**

- [ ] `qed models recommend` gives a sensible answer for your card
- [ ] You opened each Hugging Face page yourself and confirmed every repo, file and size in the catalog

**Watch out:** wrong sizes in the catalog break the 4–6 GB promise. This is the one place you must check by hand.

### Step 2.3 — Ollama and OpenAI-compatible clients

**Goal:** talk to models people already run in Ollama or LM Studio.

```text
Read AGENTS.md section 10 ("Backends"). Do step 2.3.
- runtime/ollama.py: OllamaClient (ModelClient) using /api/chat with streaming, options.num_ctx,
  options.temperature and keep_alive; is_running(); list_models() via /api/tags;
  pull(tag, on_progress) via /api/pull; unload() with keep_alive 0.
- runtime/openai_compat.py: OpenAICompatClient for any base_url (/v1/chat/completions, streaming).
- runtime/client.py: make_client(config, role="main" | "helper") factory.
- httpx with timeouts (connect 5 s, read 600 s); clear errors when no server is running.
- Tests with httpx.MockTransport. No real server in tests.
```

**Done when:**

- [ ] Tests pass
- [ ] Optional: with Ollama running and a small model pulled, a 5-line script gets an answer through `make_client`

### Step 2.4 — llama.cpp install and the shared model server

**Goal:** download the right llama.cpp build after asking, run one shared `llama-server`, and stop it when idle.

```text
Read AGENTS.md section 10 ("llama-server launch", "Server manager", "Which llama.cpp build").
Do step 2.4.
- runtime/installer.py: find_llamacpp_asset(hw, release_json) following the table;
  download_llamacpp(asset, confirm) shows the size and asks first, extracts .zip or .tar.gz to
  ~/.qedcode/bin/llama.cpp/<tag>/<backend>/, runs `llama-server --version`, and falls back
  CUDA -> Vulkan -> CPU when a build will not start.
- runtime/server_manager.py: start, reuse or stop one shared llama-server per machine with
  ~/.qedcode/run/server.json, a lock file and client registrations; free port; /health polling;
  idle unload after keep_alive_minutes; probe `--help` once per build and cache the flags.
- runtime/llamacpp.py: LlamaCppClient (ModelClient) over /v1/chat/completions with streaming.
- Tests: asset choice for every table row using a saved release JSON fixture; the server manager
  against a tiny fake server script that answers /health and /v1/chat/completions.
```

**Done when:**

- [ ] Tests pass on all three systems in CI
- [ ] On your PC: the right build downloads (CUDA on Windows, Vulkan on Linux), starts with a GGUF file and answers once
- [ ] Running two `qed doctor` commands at once does not start two servers

**Watch out:** process handling differs on Windows. Insist that the agent uses `psutil` and tests on Windows CI.

### Step 2.5 — Model downloads

**Goal:** download models safely with a size warning and a checksum.

```text
Read AGENTS.md section 10 ("Downloads"). Do step 2.5.
- installer.download_model(entry, confirm, on_progress): show the size, ask, download with
  huggingface_hub to ~/.qedcode/models/<id>/, check SHA-256 when listed, resume on retry.
- `qed models download <id>`, `qed models use <id>`, `qed models remove <id>` (asks first).
- Tests mock huggingface_hub. Never download in tests.
```

**Done when:**

- [ ] `qed models download qwen3.5-4b-q4km` shows the size, asks, downloads, and passes the checksum

### Step 2.6 — Cloud models and API keys

**Goal:** mix and cloud modes, with keys kept in the system keychain.

```text
Read AGENTS.md section 2 (rule 8) and section 10. Do step 2.6.
- runtime/cloud.py: CloudClient (ModelClient) with litellm.completion and streaming; count tokens.
- secrets: get_api_key(provider) from keyring service "qedcode" or the provider's usual env var;
  set_api_key(provider, key) stores in keyring only. Mask keys in every error and log line.
- Tests mock litellm and keyring.
```

**Done when:**

- [ ] Tests pass, and searching the repo, logs and config for a test key finds nothing

### Step 2.7 — `qed setup`

**Goal:** the one-command setup from the overview doc.

```text
Read AGENTS.md sections 10, 11 and 12. Do step 2.7: `qed setup` with Rich prompts.
Flow: detect hardware and show it (allow override) -> choose mode (local, mix, cloud) ->
local: detect Ollama and LM Studio; offer (1) the recommended model via llama.cpp, (2) another
catalog model, (3) an existing Ollama or LM Studio model or a .gguf path, (4) skip ->
list everything to download with sizes and ask once -> install -> start the model, send a
one-line test prompt, print tokens per second -> mix: choose the helper (cloud model + key,
or an Ollama model) -> cloud: provider, model, key -> if the `claude` command exists, offer
`claude mcp add qedcode -- qed mcp`; print snippets for other agents -> save config.
Re-running shows current values as defaults; --reset starts fresh.
Tests: drive the wizard with scripted answers and mocked hardware, installer and clients.
```

**Done when (Real-model check):**

- [ ] `qed setup` on your PC finishes, downloads the recommended model and prints a test answer with tokens per second
- [ ] `qed doctor` shows the model server as healthy
- [ ] With `vram_gb = 4` in the config, `qed models recommend` picks a smaller setup; with `6`, a medium one

## Milestone 3: Safe copy and edit protocol (about 6 days)

At the end of M3, QEDCode can copy a project safely, apply a model's edits inside the copy, and put an accepted change back into the user's files (and take it out again).

### Step 3.1 — Snapshots, worktrees, apply and undo

**Goal:** the safety net under everything: work happens in copies, the user's files change only on apply.

```text
Read AGENTS.md section 2 (rule 4), section 12 ("Snapshot recipe", "Apply and undo") and 13.
Do step 3.1.
- verify/run.py: the one subprocess helper (list args, cwd, timeout, scrubbed env, captured
  output, log file, psutil process-tree kill).
- vcs/git.py: run git through verify/run.py.
- vcs/snapshot.py: snapshot(root, task_id) -> commit sha with the temporary-index recipe; add
  .qed/ to .git/info/exclude first.
- vcs/worktree.py: create(root, commit, name), commit_all(path, msg), diff(base, commit),
  remove(path); `git worktree prune` on start.
- vcs/patch.py: apply_to_user(root, snapshot, patch) and undo_last(root) with the file checks.
- Tests in temp git repos: dirty tracked files, untracked files, ignored files (must NOT be
  included), a repo with no commits, paths with spaces. `git status` of the user repo is
  identical before and after a snapshot. Apply refuses when a touched file changed. Undo
  restores the files exactly.
```

**Done when:**

- [ ] Git tests pass on Windows, macOS and Linux CI
- [ ] By hand: in a scratch repo with uncommitted changes, take a snapshot and see that `git status` looks exactly the same afterwards

**Watch out:** the agent may "simplify" the snapshot with `git stash`. That touches the user's files. Reject it.

### Step 3.2 — Project detection and first-run confirmation

**Goal:** find the project's Python and tests, show them, and remember the user's answer.

```text
Read AGENTS.md section 11 ("First run in a project") and section 12. Do step 3.2.
- project/detect.py: find the interpreter (order in section 12), check pytest imports with it,
  find test files, build a ProjectConfig with the default protected paths.
- A UI-agnostic first-run function that returns the detected values for a UI to show and
  confirm; confirming saves .qed/project.toml.
- Tests with fake project folders: Windows and POSIX venv layouts, uv.lock, no venv at all.
```

**Done when:**

- [ ] On every bug zoo project the detected Python and pytest are correct

### Step 3.3 — Edit protocol parser

**Goal:** turn messy model replies into clean edit objects.

```text
Read AGENTS.md section 7. Do step 3.3: edits/protocol.py parse_reply(text) -> ParsedReply.
Implement the grammar and every leniency rule. Write at least 25 unit tests with messy replies:
prose around blocks, a missing closing fence at the end, CRLF, "edit" in lower case, quoted paths,
symbol with parentheses, SEARCH/REPLACE inside a fence, two edits on one file, no edits at all,
PLAN only, a FAILS NOW BECAUSE line.
```

**Done when:**

- [ ] 25+ parser tests pass, including the ugly ones

### Step 3.4 — The AST edit applier

**Goal:** apply edits precisely, or fail with an error a small model can act on.

```text
Read AGENTS.md section 7 ("Path rules", "Applier semantics", "Error text"). Do step 3.4:
edits/apply.py apply_edits(worktree, edits, role) -> list of errors (empty means success).
Implement all 5 ops exactly as specified, with error messages in the same style as the
examples. Enforce path rules and protected paths here too.
Tests: decorated functions, methods, nested classes, async def, a whole class replaced, a renamed
symbol (must fail), indentation re-basing, add_import with and without imports and with a module
docstring, search_replace exact / tolerant / zero / many matches, create_file on an existing file
(must fail), CRLF files keep CRLF.
Last: a test that applies every bug zoo edits.txt to a copy of its project and checks that the
hidden tests then pass.
```

**Done when:**

- [ ] All 10 bug zoo `edits.txt` files apply and make the hidden tests pass
- [ ] Parser and applier together have 60+ passing tests

**Watch out:** this is the heart of reliability for small models. Use your strongest AI model here and read the code for `replace_symbol` yourself.

## Milestone 4: Checks, grades and proof receipts (about 6 days)

At the end of M4, `qed check` can grade any change in a project and write a receipt that `qed verify` can re-check. No AI model is involved yet.

### Step 4.1 — pytest runner and baseline

**Goal:** run tests the same way every time and read their results reliably.

```text
Read AGENTS.md section 8 ("Targeted test selection", "pytest command"). Do step 4.1.
- verify/pytest_runner.py: run_pytest(worktree, python, selection, timeout) -> TestResults and a
  CheckResult, parsing JUnit XML (key = classname::name). Handle "no tests collected",
  collection errors and timeouts.
- verify/baseline.py: baseline(snapshot, project, full, related) -> Baseline, in its own worktree.
- project/context.py, first part: related_tests(changed_paths, repo) with the selection rules.
- Tests on bug zoo copies (real pytest runs are fine here; they are fast and need no GPU).
```

**Done when:**

- [ ] Baseline on bug zoo 001 lists every visible test as passed
- [ ] A test that sleeps longer than the timeout is killed and reported, on Windows too

### Step 4.2 — Lint, scope and risk guards

**Goal:** catch broken, out-of-bounds or dangerous changes before any test runs.

```text
Read AGENTS.md section 8 (stages scope, risk and lint; protected paths; risky patterns).
Do step 4.2.
- verify/lint.py: new-errors-only comparison with ruff's fatal rules, base vs candidate.
- verify/guard.py: scope_check(diff, project, role, speed) and risk_check(diff) -> CheckResult
  (risk sets needs_approval).
- Tests: every protected pattern, skip/xfail/assert-removal detection, a deleted file, a diff
  over the size limit, every risky pattern, a new lint error vs an old one.
```

**Done when:**

- [ ] A candidate that edits `tests/test_pages.py` is rejected at `scope` with a clear message
- [ ] A candidate that adds `import subprocess` is flagged at `risk`

### Step 4.3 — Cascade, grades, receipts and verify

**Goal:** one function that turns a candidate into a grade with proof.

```text
Read AGENTS.md sections 5, 6 ("evaluate") and 8. Do step 4.3.
- verify/cascade.py: run_cascade(candidate, base, baseline, repro, project, speed, confirm) runs
  the stages in order, stops at the first failure, and emits Events through a callback.
- verify/grade.py: exactly the function in section 8. Nothing else may set a grade.
- verify/receipt.py: build and save receipts, plus the one-line summary.
- verify/verify.py and `qed verify <receipt.json>`.
- Tests: every branch of the grade function; a receipt round trip; verify passes on an untouched
  receipt and reports a mismatch after the patch text inside the JSON is edited.
```

### Step 4.4 — `qed check`

**Goal:** grade the user's own uncommitted changes (the "check my work" feature).

```text
Read AGENTS.md section 6 ("check"). Do step 4.4: engine/check.py and
`qed check [--speed] [--base] [--json]`, using the cascade with role "user". Print the grade, the
one-line summary and any failing tests with Rich.
```

**Done when:**

- [ ] In a copy of bug zoo 001, apply `solution.patch` by hand and run `qed check`: grade **B** (tests pass, but nothing proved the bug)
- [ ] Copy `hidden/test_hidden.py` into `tests/` and run `qed check` again: grade **A** (the new test fails on the old code and passes now)
- [ ] `qed verify .qed/receipts/<id>.json` prints `VERIFIED`

**Watch out:** if the grade comes out A without a fail-before test, the grade code is wrong. Do not move on until B and A appear exactly as above.

## Milestone 5: The solve loop (about 10 days)

At the end of M5, `qed solve "..."` works end to end: the model makes tries, QEDCode tests them, repairs near-misses and returns a graded, explained fix. This is the milestone where QEDCode becomes real.

### Step 5.1 — Roles and prompts

**Goal:** six small, reliable model calls with exact reply formats.

```text
Read AGENTS.md sections 7 and 9. Do step 5.1.
- engine/prompts/*.txt: the templates from section 9, word for word.
- engine/roles.py: intake(), locator(), test_writer(), coder(), repairer(), explainer(). Each
  builds its prompt, calls ModelClient.chat with the role's temperature, max tokens and
  tag=<role name>, parses the reply, does one format re-ask on a parse failure, and returns
  typed results.
- Context budget: estimate tokens as characters / 3.5 and trim as section 9 says.
- Save every prompt and reply under .qed/runs/<task id>/.
- Tests with FakeModel: good replies; a badly formatted reply then a good one; a reply that is
  still bad after the re-ask.
```

**Done when:**

- [ ] Role tests pass, and the saved prompts in `.qed/runs/` read cleanly

### Step 5.2 — Context: repo map, search, locator

**Goal:** give the model the right files and nothing else.

```text
Read AGENTS.md sections 6 and 9. Do step 5.2.
- project/repo_map.py: files plus top-level symbols via ast, within a token budget.
- project/search.py: keywords from the task (identifiers, quoted text, error messages, file
  names); search with ripgrep when installed, otherwise pure Python.
- project/context.py build(): run the locator, validate paths, fall back to the top search hits,
  and assemble file contents within the budget.
- Tests on the bug zoo and on a generated fixture repo with 200 small files.
```

**Done when:**

- [ ] For every bug zoo task, the selected files include the buggy file

### Step 5.3 — The controller

**Goal:** the brain of QEDCode: tries, stop rules, repair, selection.

```text
Read AGENTS.md section 6 completely. Do step 5.3: engine/solve.py, engine/select.py,
engine/budget.py and engine/api.py (solve, check, verify, apply, undo).
Follow the controller pseudocode and the speed dial table exactly. Emit Events for every stage.
Respect cancel and the time budget. Clean up worktrees at the end (keep the winning candidate's
commit until it is applied or the next task starts).
Tests with FakeModel on the bug zoo:
1) a good coder reply -> grade A at normal speed;
2) a broken first reply, then a good repair -> grade A through repair;
3) the coder edits a test file -> rejected at scope, then a good reply wins;
4) the test writer's test passes on the base -> repro dropped, target becomes B;
5) every reply broken -> status no_fix with the closest attempt;
6) fast speed -> one try, no test writer, grade B;
7) cancel in the middle -> status cancelled and no worktrees left.
```

**Done when:**

- [ ] All seven scenarios pass
- [ ] Every one of the 10 bug zoo scripts reaches grade A with FakeModel

**Watch out:** agents like to merge the loop, the checks and the grading into one giant function. Keep them in their own modules as the brief says; it makes every later feature easier.

### Step 5.4 — Helper escalation and `qed solve`

**Goal:** mix mode, plus the command people will use from scripts and CI.

```text
Read AGENTS.md section 6 (helper) and section 11. Do step 5.4.
- Mix-mode helper escalation through cb.confirm, using make_client(config, role="helper") and
  the compact context.
- `qed solve "<task>" [--speed] [--apply] [--json] [--allow-risky]` with a Rich live progress
  view, then the summary, the explanation and the receipt path. Non-interactive callbacks:
  ask -> None, confirm -> False unless flags or config pre-approve.
- Tests: the helper is used when confirmed and skipped when not; --json output matches SolveResult.
```

**Done when (Real-model check — your first real numbers):**

- [ ] Run `qed solve` with your local model on all 10 bug zoo tasks at **normal** speed, using each `task.txt`. Write in `PROGRESS.md` how many ended with grade A, grade B and no fix, plus the time per task
- [ ] Run the same 10 tasks at **fast** speed: roughly "the plain small model, one try". Compare the two
- [ ] Copy each project's hidden test in afterwards and check which "passed" fixes were really right; every wrong grade A is a bug to fix before M6

This is a quick sanity check, not the big benchmark; that comes after v1.

## Milestone 6: The terminal interface (about 6 days)

At the end of M6, typing `qed` in a project folder opens the screen from the overview doc, and a beginner can fix, apply and undo without reading any docs.

### Step 6.1 — The TUI shell

**Goal:** the screen layout, with tasks running in the background and progress streaming in.

```text
Read AGENTS.md section 11 ("TUI"). Do step 6.1: tui/app.py with Textual: header (model, backend,
GPU, status), a speed and mode line, a scrolling log, the task input and a footer with keys.
Running a task calls engine.api.solve in a Textual worker; Events reach the log through
call_from_thread. Esc cancels. `qed` with no arguments opens it in the current folder.
Tests with Textual's run_test pilot and FakeModel.
```

**Done when:**

- [ ] With FakeModel, a typed task shows its steps live in the log, and the screen never freezes

### Step 6.2 — Results view

**Goal:** show tries, the grade and the fix clearly.

```text
Do step 6.2 (AGENTS.md section 11): candidate cards (id, stage reached or grade), a result panel
(grade, one-line summary, explanation), a full-screen diff with syntax highlighting (d), receipt
info (r) and a help screen (?). Keep the style calm: one accent colour for grade A, plain text
for everything else.
```

### Step 6.3 — Apply, undo, try harder, dialogs

**Goal:** every action from the overview doc works from the keyboard.

```text
Do step 6.3 (AGENTS.md section 11): a = apply (confirm first for grade C or D), u = undo,
h = try harder (rerun at the next speed, starting from the best candidate), m = cycle speed,
plus the dialogs: risky-pattern approval (show the lines), helper approval (say that code will
leave the computer for cloud helpers) and the intake question. Implement the Callbacks protocol
for the TUI.
```

### Step 6.4 — First run and model lifecycle

**Goal:** a smooth first minute, and a GPU that is freed when you leave.

```text
Do step 6.4 (AGENTS.md sections 10 and 11): when `qed` starts with no global config, offer to
run setup; in a folder that is not a git repo, offer `git init` plus a first commit; with no
project config, show the detected commands and ask to confirm. Load the model through the server
manager with a "Loading model..." line while the input stays usable. On quit, unload per config.
```

**Done when:**

- [ ] In a bug zoo copy: run `qed`, type the task, watch the tries, press `a` and see the files change; press `u` and see them return
- [ ] Esc during a task stops it within a few seconds and leaves no folders in `.qed/worktrees/`
- [ ] After `q`, `nvidia-smi` shows the model gone from VRAM (when no other client uses it)
- [ ] It looks right in Windows Terminal, the macOS Terminal and a Linux terminal

## Milestone 7: MCP server and agents (about 3 days)

At the end of M7, Claude Code, OpenCode and other agents can call QEDCode to fix and to check code, while sharing the same model server as the TUI.

### Step 7.1 — The MCP server

**Goal:** the four tools from the brief, safe to call from any agent.

```text
Read AGENTS.md section 11 ("MCP server"). Do step 7.1: mcp_server.py using FastMCP from the
official `mcp` package; tools solve, check, verify and status exactly as the table says; a
background job runner with the project lock; progress notifications when the client supports
them; non-interactive callbacks. `qed mcp` starts it over stdio.
Tests: call the tool functions directly with FakeModel; a second call while busy; status polling.
```

### Step 7.2 — Connect the agents

**Goal:** one command for Claude Code, copy-paste snippets for the rest.

```text
Do step 7.2: in `qed setup`, offer to run `claude mcp add qedcode -- qed mcp` when the `claude`
command exists, and print snippets for OpenCode, Cline and Cursor with links to their MCP docs.
Add a "Use with agents" section to README.md.
```

**Done when:**

- [ ] In Claude Code, inside a bug zoo copy: "Use the qedcode tool to fix the bug described in task.txt". Claude Code calls `solve` and reports the grade and the receipt path
- [ ] Ask Claude Code to make a change itself, then "check it with qedcode": `check` returns a grade
- [ ] With the TUI and an agent both using QEDCode, only one `llama-server` process runs (Task Manager or `ps`)

**Watch out:** solves take minutes, and some agents give up on slow tools. Rely on `status` polling, and see Troubleshooting for timeouts.

## Milestone 8: Polish and release v1 (about 5 days)

At the end of M8, a stranger can install QEDCode from PyPI and fix a bug within about 10 minutes plus download time.

### Step 8.1 — Doctor, error messages, logs

**Goal:** every problem a user can hit comes with a clear fix.

```text
Do step 8.1: finish `qed doctor` (git, project Python, pytest in that Python, ruff, hardware,
engine build, model file, model server health, free disk space for models) with a fix for every
failed check. Go through every user-facing error in the codebase and make it say what happened,
why, and what to do next (AGENTS.md section 2, rule 12). Add log rotation.
```

### Step 8.2 — Uninstall and cleanup

```text
Do step 8.2: `qed uninstall` removes the project's .qed/; `qed uninstall --all` also removes
~/.qedcode after listing what will be deleted and its size and asking. Stop any QEDCode-owned
model server first.
```

### Step 8.3 — Real hardware pass (you do this, not the AI)

- [ ] Windows with your RTX 5060 Ti: setup, solve, apply, undo, check, verify, MCP
- [ ] Linux or WSL2: the same flow with the Vulkan build
- [ ] An AMD or Intel machine, or CPU-only, if you can borrow one
- [ ] A Mac with Apple Silicon, if you can borrow one
- [ ] Set `vram_gb = 4`, then `6`; run three bug zoo tasks each and watch peak VRAM in `nvidia-smi` stay under the budget

### Step 8.4 — Release

```text
Do step 8.4: README with a 30-second demo GIF (record with vhs or asciinema) and install
instructions (`uv tool install qedcode` or `pipx install qedcode`); LICENSE (<MIT or Apache-2.0,
you choose>); CHANGELOG.md; version 0.1.0; a GitHub Actions workflow that publishes to PyPI with
trusted publishing when a tag v* is pushed.
```

**Done when:**

- [ ] On a clean machine: `uv tool install qedcode`, `qed setup`, `qed`, and a bug zoo task fixed in about 10 minutes plus download time
- [ ] Pushing the tag `v0.1.0` puts the package on PyPI
- [ ] The README's grade table and limits match what v1 really does

## After v1: the order for future features

Build the future features in this order: each one reuses parts that already exist, so no step is a rewrite. Before starting one, add a short section for it to `AGENTS.md` (what it does, which modules it touches, its v1 limits), then plan its steps the same way as above.

| Order | Version | Feature | Why at this point | Builds on |
| --- | --- | --- | --- | --- |
| 1 | v1.1 | Learns your project | Cheap to add and improves every later task | Receipts and `.qed/runs/` logs, SQLite FTS5 search |
| 2 | v1.1 | Sealed test box | Removes v1's biggest safety limit | `verify/run.py` gets a Docker runner with the network off |
| 3 | v1.1 | JavaScript and TypeScript | Roughly doubles who can use it | tree-sitter in `edits/`, jest or vitest in `verify/` |
| 4 | v1.1 | Quiet mode, one-click agent connect | Small and visible to every user | Server flags, `qed setup` |
| 5 | v1.2 | Safety Net Builder (`qed tests`) | Reuses the test writer and the fail-before check | Test writer, cascade |
| 6 | v1.2 | Bug Hunter (`qed hunt`) | The Safety Net Builder pointed at bugs instead of gaps | Safety Net Builder |
| 7 | v1.2 | Watch Mode | A file watcher that starts a solve when a test breaks | Solve loop, shared server |
| 8 | v1.2 | Smart questions (A or B) | Differential testing on candidates that all pass | `engine/select.py` |
| 9 | v1.3 | Night Shift (`qed night`) | A queue of jobs plus a morning report | MCP job runner |
| 10 | v1.3 | Web dashboard (`qed ui`) | A second view on the same engine events | `engine.api` |
| 11 | v1.3 | Big Brain mode | Swap in a large MoE model with `--n-cpu-moe` for hard tasks | Server manager |
| 12 | v1.3 | Deep check | `qed check` also writes tests | Check plus test writer |
| 13 | v2 | PR proof badges, Smart speed, Trained helpers, Comment checker, Leaderboard, Offline pack | These need real usage data or more polish first | Everything above |
| 14 | Research | Stop-or-continue judge (TRM idea) | Needs logs from many tasks; keep it only if it beats a simple model | Run logs |

**The benchmark, when you are ready.** Start from the bug zoo, add 40–60 real tasks made by reverting real bug-fix commits, hide their tests, and compare QEDCode with Aider and with Claude Code running the same model, three runs each. Section 9 of `PLAN.md` in this project describes the full method.

## Troubleshooting and when you are stuck

Most problems come from three places: too little VRAM, the AI agent drifting from the brief, or Windows details. Find your symptom below.

| Problem | Likely cause | What to do |
| --- | --- | --- |
| The model will not load, or runs out of memory | Context or model too big for the card | Lower `ctx` to 8192, keep `kv_cache = "q8_0"`, run `qed models recommend` and pick what it says |
| AMD or Intel GPU not detected | Driver or detection gap | Set vendor and VRAM by hand in `qed setup`; use the Vulkan build; update the GPU driver |
| Answers are garbage or ignore the format | Wrong chat template, or temperature too high | Make sure `llama-server` runs with `--jinja`; use an instruct model; check the role temperatures |
| The model keeps breaking the edit format | That model struggles with some edit ops | Look at the edit-apply failure rate in `.qed/runs/`; prefer `replace_symbol` in the prompt; try another model |
| Tests hang | A test waits for the network or for input | Lower `test_timeout_seconds` in `.qed/project.toml`; the sealed test box in v1.1 fixes this for good |
| Leftover worktrees or git worktree errors | A run crashed | `git worktree prune`, then delete `.qed/worktrees/` |
| Path errors on Windows | Long paths or spaces | `git config --global core.longpaths true`; keep repos in short folder paths |
| Everything is very slow | The model runs on the CPU by mistake | Watch `nvidia-smi` during a solve; check `-ngl 99` and that the GPU build is installed; use Fast speed |
| The agent says the MCP tool timed out | Solves take minutes | Let the agent poll `status`; raise the agent's MCP tool timeout (in Claude Code, the `MCP_TOOL_TIMEOUT` setting; check its docs) |
| The AI coding agent goes in circles | The step is too big, or it lost context | `git restore .`, split the step, paste the exact failing output, remind it to read AGENTS.md |
| The AI changed AGENTS.md or added dependencies | Drift | Revert those files; add "Do not edit AGENTS.md or add dependencies" to your prompt |

**When you are stuck for more than an hour:**

1. Stop and go back to the last green commit.
2. Ask the AI to explain, in plain words, what the failing code is supposed to do. Wrong explanations show where the bug is.
3. Write one failing test that shows the problem, then ask the AI to make only that test pass.
4. Start a fresh session with only `AGENTS.md`, `PROGRESS.md` and the files involved.
5. If the step itself seems wrong, change the plan on purpose: write the change into `PROGRESS.md` so future sessions know.
