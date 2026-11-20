# Changelog

All notable changes to QEDCode. The format follows [Keep a Changelog](https://keepachangelog.com/).

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
