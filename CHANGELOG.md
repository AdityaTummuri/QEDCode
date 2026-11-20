# Changelog

All notable changes to QEDCode. The format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- **Learns your project** (v1.1): applied A/B fixes are remembered in `.qed/memory.db` (SQLite FTS5)
  and similar ones are shown to the local model as examples. `qed memory [list|forget|clear]`,
  `[engine] learn`.

## [0.1.0] - 2026-11-20

First release: Python projects tested with pytest.

### Added
- **Engine**: snapshot + git worktrees, intake, locator, test writer, k candidate tries, repair,
  helper escalation (mix mode), selection, evidence grade A-D, proof receipts and `qed verify`.
- **Checks**: edit applier on Python's `ast`, scope and test-tamper guard, risky-pattern guard,
  ruff fatal rules (new errors only), pytest targeted and full suite with a baseline of known failures.
- **Interfaces**: `qed` terminal UI (Textual), CLI (`setup`, `solve`, `check`, `verify`, `apply`,
  `undo`, `models`, `doctor`, `uninstall`, `mcp`) and an MCP server over stdio
  (`solve`, `check`, `verify`, `status`).
- **Models**: llama.cpp (`llama-server`, auto-installed after asking, one shared server per
  machine), Ollama, any OpenAI-compatible server such as LM Studio, and cloud models through LiteLLM
  with API keys in the system keychain. Hardware detection for NVIDIA, AMD, Intel, Apple Silicon and
  CPU-only, with a model and context recommendation that fits the VRAM.
- **Speed dial**: fast, normal, sure. **Modes**: local, mix, cloud.
- **Safety**: worktree isolation, fixed command allowlist, secret scrubbing, timeouts, asking before
  every download, apply/undo that refuse when files changed.
- **Bug zoo**: 10 tiny buggy projects and a checker, used to test the engine end to end.

### Known limits
- Python and pytest only. Tests run on your computer without network blocking or a container; every
  receipt lists this under "not proven".
- The real-model numbers on the bug zoo and the hardware passes (Windows, Linux, AMD/Intel, Apple
  Silicon) were not part of the automated build; see PROGRESS.md.
