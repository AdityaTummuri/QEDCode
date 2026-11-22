# 0005: SWE-bench Verified, not Terminal-Bench, as the standard suite

**Status:** Accepted (Benchy milestone 7, 2026-11-21)

## Context

`benchy-core` (eight small tasks) proves Benchy's pipeline but is too small and too easy to
persuade anyone. Benchy needed a recognised standard suite.

## Decision

Terminal-Bench was considered and rejected: its tasks need an agent with a shell, and QEDCode by
design never lets a model run commands ([0001](0001-model-never-runs-commands.md)), so it would
measure something QEDCode does not do. SWE-bench Verified matches what QEDCode does: patch a Python
repository so its tests pass, judged by hidden fail-to-pass tests. Benchy runs 143 of its tasks
(sympy, sphinx, pylint, xarray) without Docker, using uv environments.

Public tasks can be remembered by models, so Benchy also has private "fresh" suites: new bugs
planted in real projects, whose answers are never published.

## Consequences

- Results are comparable with a benchmark people know.
- About 4 GB of environments to download for the full SWE-bench suite.
- Fresh suites can only be reported as totals; their tasks stay private.
