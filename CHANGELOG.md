# Changelog

All notable changes to QEDCode. The format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

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
