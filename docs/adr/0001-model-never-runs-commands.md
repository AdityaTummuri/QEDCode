# 0001: The model never runs commands

**Status:** Accepted (2026-11-20, `AGENTS.md` rule 2)

## Context

General coding agents let the model choose shell commands. That is flexible, but it makes every
model mistake, and every prompt injection hidden in a README, comment or issue, a possible command
on the user's computer. QEDCode runs small local models that are wrong often enough that their
output has to be treated as untrusted.

## Decision

Only fixed commands built by QEDCode code run: git, the project's Python with pytest, ruff, the
project's Node test runner and the llama.cpp server. `shell=True` is never used. Model text is
never a command or an argument, except file paths that are checked to sit inside the worktree and
may not contain quotes or control characters or start with `-`. The model returns text edits in a
plain-text protocol, and a strict applier checks them.

## Consequences

- A whole class of attacks (prompt injection into commands) has nowhere to land.
- QEDCode cannot do tasks that need a shell, such as installing packages or running migrations.
  That is also why Terminal-Bench is not its benchmark ([0005](0005-swe-bench-not-terminal-bench.md)).
- The command allowlist lives in code, so adding a test runner is a code change with a review,
  not a prompt.
