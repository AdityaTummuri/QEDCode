# Changelog

All notable changes to QEDCode. The format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Changed
- **The default install is light** (about 70 MB instead of 300). The terminal UI, cloud models and
  model downloads are extras: `qedcode[tui]`, `[cloud]`, `[download]` or `[all]`.
- **`qed try` shows the check**: three changes an agent might hand in, graded with no model, in about
  12 lines. The old tour, where a model writes the fix, is `qed try --solve`.
- **Fewer model calls at fast and normal speed.** No intake call for a clear request, no locator call
  on a small project, an explanation written from the receipt, one small repro test instead of up
  to three, and the tests run while the model writes. `sure` speed is unchanged.
- `qed --help` lists eight commands; the others still work.
- The GitHub Action installs QEDCode from its own ref (no PyPI needed) and the pre-commit hook uses
  `--fail-on-tampered`.

### Fixed
- `qed try` and every git command failed when git settings came from the environment
  (`GIT_CONFIG_COUNT`): the secret filter dropped the keys and kept the count. The whole family is
  dropped now.
- Docs: the first laptop runs had 11 silent failures from the bare models, not 14.

### Added
- **Caught.** `qed check` says when a change makes tests look green: skips, always-true asserts,
  deleted tests, collection switched off, or a failing test edited to pass (the original test is run
  against the new code). `--fail-on-tampered`, `tampered` in `--json` and in the MCP tool, and the
  GitHub Action fails on it by default (`fail-on`).
- `qed hook install claude`: Claude Code's stop step runs the check.
- **A second repro test** when two tries fail the first one the same way; a result that needed it is
  graded B at most and says so.
- `Receipt.timings`: seconds per step.
- `qed try` (and `uvx qedcode try`): the whole loop on a tiny bundled project in about a minute,
  with no GPU. It shows a try that cheats (a `conftest.py` that switches the failing test off)
  being rejected, the real fix reaching grade A, and `qed verify` returning VERIFIED. With no model
  set up it uses a scripted model and says so; `--real` uses your model.
- A `qedcode` command beside `qed`, so `uvx qedcode` works.
- **Proof strength (mutation testing).** For a result with a test that failed before and passes
  after, QEDCode now breaks the changed lines in small ways (`<` to `<=`, `+` to `-`,
  `return None`, ...) and re-runs the new tests on each. The summary line says
  `proof strength 8/9` and the receipt lists the mutants that survived. Grades are unchanged.
- `qed receipt [file] [--link] [--json] [--commit <ref>]`: a receipt in plain English, or a link
  that opens it in the Receipt Viewer with the whole receipt inside the link (nothing is
  uploaded). `qed verify` accepts such a link.
- `qed check --fail-below <grade>` for CI and pre-commit, and a "To raise the grade: ..." line
  (also `next_step` in `--json` and in the MCP `check` tool).
- `qed apply --commit`: commit an applied fix with `QED-Grade` and `QED-Receipt` trailers and its
  receipt in git notes (`git log --notes=qed`).
- **Smart Questions.** When two tries pass every test but differ, QEDCode finds an input where
  they disagree and asks which behaviour is right; the answer becomes a test. At `sure` speed one
  extra try looks for such a disagreement after the first passing one.
- `qed tests`: the Safety Net Builder writes tests for functions no test mentions and keeps only
  the ones that pass today and catch mutants.
- `qed hunt`: the Bug Hunter writes tests for suspected bugs, keeps the ones that fail on
  today's code, and tries a proven fix for each. Nothing is applied.
- `qed night add|list|clear|run|report`: the Night Shift queues tasks, solves them unattended
  in quiet mode and writes one HTML report.
- The website (GitHub Pages): a landing page, the Receipt Viewer (open a receipt link or file,
  schema and checksum checked in the browser), **Fool the Grader** (try to cheat QEDCode's real
  grading rules, running in your browser with Pyodide, judged by a hidden test) and the docs.
- The QEDCode Check GitHub Action (`uses: AdityaTummuri/QEDCode@v0.2.0`): grades a pull request
  with `qed check`, posts one sticky comment with the next step and a Receipt Viewer link, can
  fail below a grade, works safely for pull requests from forks, and can sign receipts with
  GitHub artifact attestations. A pre-commit hook (`id: qedcode-check`) as well.
- `qed connect antigravity` and `qed connect vscode`: register QEDCode's MCP server with Google
  Antigravity (through `agy mcp add`, or a careful merge of `~/.gemini/config/mcp_config.json`
  with a backup) and with VS Code (through `code --add-mcp`). `qed setup` offers them too. The
  README explains how to use QEDCode from these IDEs with a cloud model, so no GPU is needed.
- `qed benchy [status|install|uninstall|run|report|selftest|suites|share]`: optional link to
  [QEDCode Benchy](https://github.com/AdityaTummuri/QEDCode-benchy), which compares QEDCode with
  the same model used on its own. Installing asks first; nothing runs unless you type the command.
  `qed setup` ends with a one-line pointer to it.

### Changed
- `qed check` no longer lists a new test file you added as a scope problem ("only the test
  writer may add tests" is for model tries); your own new test is evidence. The "To raise the
  grade" advice names a weakened test before anything else.
- Big repositories no longer stall the solve before the first try. At normal and sure speed the
  full suite gets 120 s / 300 s before the change; a slower suite is marked too slow for the task
  (not run on the finalist either, so the best grade is B) and the related tests run instead.
  Related tests now run one file at a time with a 60 s limit each; slower files are left out of
  every run and named in the receipt. Before, one slow file (sympy's `test_wester.py`, about
  380 s) made the whole run time out after 300 s, which capped the result at C, and at fast speed
  used the whole time budget before the model was asked anything. Found by Benchy's SWE-bench
  suite.
- `qed check` uses the same full-suite limit (it waited the project's 300 s test timeout instead;
  on sympy one check took 306 s, now 126 s at normal speed and 6 s at fast). A project remembers
  that its full suite did not finish (`.qed/full_suite_too_slow.json`), so later checks and solves
  go straight to the related tests instead of waiting for the same timeout again. Found by
  Benchy's agent mode.

### Fixed
- A try may no longer edit the test QEDCode wrote to show the problem. Repairs on real hardware
  rewrote that test (once to add its missing import, once to match their own fix), and the edited
  test then counted as proof: three wrong grade A results in the laptop runs. Such a try is now
  rejected at the scope check. A new repro test that uses a name it never imported is asked for
  again instead of being used.
- A repair that edited the repro test (for example to add the import it was missing) no longer
  reuses the old "failed before" list: the edited test runs on the old code again, so a test
  that passes there is never counted as proof. Found on the first real-hardware run, where
  `qed verify` correctly returned NOT VERIFIED for such a grade A.
- `qed doctor` and model recommendations detect the GPU even when a config was saved before
  `qed setup` ran (it held only the default "cpu" entry).
- The edit parser no longer splits a SEARCH/REPLACE block at a reST heading underline inside the
  code (`    ========`, as in sympy and numpy docstrings). The exact `=======` marker now wins over
  a loose one; a loose divider still works when the block has no exact one. Any edit whose
  SEARCH text contained such a docstring was cut in half and failed with a syntax error. Found
  by Benchy's new SWE-bench suite on a real sympy task.
- `qed verify` (and the MCP `verify` tool) re-runs tests with the project's detected Python, the
  same one `qed check` uses, when the project settings were never confirmed in the terminal UI.
  It used QEDCode's own interpreter instead, so with QEDCode installed as a `uv tool` (no pytest
  there) a sound receipt from an agent or MCP run came back NOT VERIFIED. Found by the agent
  skill's new contract test in CI.
- A candidate that changes no code outside tests (only comments or formatting, judged by the
  Python syntax tree) is now graded C instead of B. Its tests passed only because nothing changed.
  The receipt names the gap. Found by Benchy's scripted self-test.
- The same C cap now covers changes that only touch files no test can run or read: `.gitignore`,
  `.gitattributes`, `.editorconfig`, licences, Markdown/reST and `docs/` (docs count again when
  pytest runs them through `--doctest-glob`). A one-line `.gitignore` edit in a project with
  passing tests was graded B ("tested"). Config such as `pyproject.toml`, requirements, JSON or
  YAML still counts as a code change. Found while testing `qed check` from the Antigravity IDE.
  The one-line summary ends with `no code changed outside tests` so the C is explained there too.

### Changed
- **License**: QEDCode is now licensed under the Apache License 2.0 (was GNU GPL-3.0). A `NOTICE`
  file carries the copyright notice, and the package metadata uses the SPDX expression
  `Apache-2.0`. Versions up to and including commit `a7fbe17`, including the 0.1.0 entry below,
  were released under GPL-3.0.

### Security
- `qed verify` and the MCP `verify` tool no longer run whatever a receipt file asks for. Only the
  project's own test command (or the default `{python} -m pytest`) may run, and the test selection
  may name only files and test ids inside the project, so a crafted receipt cannot start another
  program or pass pytest options such as `--basetemp` that delete folders.
- QEDCode's own git commands never run git hooks (`core.hooksPath` points nowhere), so neither the
  project's hooks nor a hook file written by a candidate can run during snapshots or candidate
  commits.
- Inherited `GIT_DIR`, `GIT_INDEX_FILE` and the other git location variables are ignored. Running
  `qed check` from a pre-commit hook could otherwise write to the user's own index.
- Paths written by the model may not contain quotes or control characters, or start with `-`.
  The diff reader now also decodes the quoted names git prints for such files, so the scope and
  risky-code checks can no longer miss one.
- `qed check --base` (and the MCP `check` tool) refuses a base that looks like an option.

### Fixed
- `qed check` no longer treats a regression test appended to an existing Python test file as
  tampering. When the file only gains tests (compared by syntax tree), the new tests run as the
  fail-before/pass-after proof, so the change can reach grade A; the receipt lists the file so
  `qed verify` re-runs them. Only the added tests count as proof.
- `qed check` caps a change that weakens a test at grade C (it was B): an added skip or xfail,
  removed asserts, or a new assert that can never fail (`assert True or ...`, `assert 1`,
  `expect(true).toBe(true)`). The summary adds `tests weakened in <files>` and the receipt
  explains the gap.
- When the tests cannot run at all (for example pytest is missing from the chosen Python), the
  summary says `tests could not run: <reason>` instead of `no tests ran`, and `qed check` adds a
  note pointing to `qed doctor`.
- MCP: `check` now returns `notes` (which Python was used, why tests could not run), and the
  server instructions and `solve` description no longer say the engine works only with Python.
- The project lock is no longer taken over while its owner is still writing it (two jobs could
  start at once), and a lock whose process id was reused by a newer process no longer keeps a
  project busy for ever.
- The shared model server is restarted when the context size or the KV cache type changes, instead
  of reusing a server with a smaller context.
- A long streaming answer keeps the shared model server marked as in use, so another QEDCode
  window's idle check can no longer stop it in the middle of an answer.
- The lint stage no longer crashes on a base file that is not valid UTF-8.
- Each candidate's test logs and reports are kept in their own folder under `.qed/runs/<task>/`,
  so a later try no longer overwrites the logs a receipt points to.
- MCP: a job that crashes is logged with its traceback, and finished jobs no longer pile up in
  memory in a long-running `qed mcp` server.
- Windows: JavaScript and TypeScript tests with `node --test` work (the reporter is passed to Node
  as a `file://` URL; a plain `C:\...` path was rejected, so every Node run failed).
- Windows: `qed memory clear` can replace a damaged memory database (a connection to the broken
  file was left open, which locks it on Windows).
- Windows: `qed uninstall --all` closes QEDCode's own log file before deleting `~/.qedcode`, and
  uninstall never reports a folder as removed when it could not be removed.
- The terminal UI no longer crashes when its status refresh fires while a dialog is open or the
  app is closing.
- Windows: releasing the model-server lock or the project lock retries when another QEDCode
  process is reading the file at that moment, instead of leaving the lock stuck (every other
  client then waited 60 seconds and failed).
- The repository keeps LF line endings on every OS (`.gitattributes`), so the bug zoo and test
  fixtures are the same on Windows.

### Changed
- When no related tests are found and the suite is quick, the whole suite runs once and counts as
  the full-suite check, instead of running it twice (targeted, then full).

## [0.1.0] - 2026-11-20

First release.

### Added
- **Engine**: snapshot + git worktrees, intake, locator, test writer, several tries, repair, helper
  escalation (mix mode), selection, evidence grade A-D, proof receipts and `qed verify`.
- **Checks**: edit applier (Python `ast`, tree-sitter for JavaScript/TypeScript), scope and
  test-tamper guard, risky-pattern guard, ruff fatal rules (new errors only, Python), tests run
  targeted then full with a baseline of known failures.
- **Languages**: Python (pytest) and JavaScript/TypeScript (`node --test`, Vitest, Jest).
- **Interfaces**: `qed` terminal UI (Textual), CLI (`setup`, `solve`, `check`, `verify`, `apply`,
  `undo`, `models`, `doctor`, `uninstall`, `memory`, `sandbox`, `quiet`, `connect`, `mcp`) and an MCP
  server over stdio (`solve`, `check`, `verify`, `status`).
- **Models**: llama.cpp (auto-installed after asking, one shared server per machine), Ollama, any
  OpenAI-compatible server such as LM Studio, and cloud models through LiteLLM with API keys in the
  system keychain. Hardware detection for NVIDIA, AMD, Intel, Apple Silicon and CPU-only.
- **Speed dial** (fast, normal, sure) and **modes** (local, mix, cloud).
- **Learns your project**: applied A/B fixes are remembered in `.qed/memory.db` and similar ones are
  shown to the local model as examples.
- **Sealed test box**: `qed sandbox` runs Python tests in Docker with the network off.
- **Quiet mode**: `qed quiet [on|off|auto]` for cooler, quieter runs on laptops.
- **One-click agent connect**: `qed connect` and the last step of `qed setup` register `qed mcp` with
  Claude Code, OpenCode, Cline and Cursor, after asking.
- **Safety**: worktree isolation, fixed command allowlist, secret scrubbing, timeouts, asking before
  every download, apply/undo that refuse when files changed, `qed doctor`, `qed uninstall`.
- **Bug zoo**: 10 tiny buggy projects and a checker, used to test the engine end to end.

### Known limits
- By default tests run on your computer without network blocking; every receipt lists this under
  "not proven". The sealed test box (Docker) covers Python only.
- No linter stage for JavaScript/TypeScript.
- Real-model quality numbers and the hardware passes (Windows, Linux, AMD/Intel, Apple Silicon,
  Docker) were not part of the automated build; see PROGRESS.md.
