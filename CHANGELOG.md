# Changelog

All notable changes to QEDCode. The format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- `qed connect antigravity` and `qed connect vscode`: register QEDCode's MCP server with Google
  Antigravity (through `agy mcp add`, or a careful merge of `~/.gemini/config/mcp_config.json`
  with a backup) and with VS Code (through `code --add-mcp`). `qed setup` offers them too. The
  README explains how to use QEDCode from these IDEs with a cloud model, so no GPU is needed.
- `qed benchy [status|install|uninstall|run|report|selftest|suites|share]`: optional link to
  [QEDCode Benchy](https://github.com/AdityaTummuri/QEDCode-benchy), which compares QEDCode with
  the same model used on its own. Installing asks first; nothing runs unless you type the command.
  `qed setup` ends with a one-line pointer to it.

### Fixed
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
