# Contributing

Thanks for helping. QEDCode is small on purpose, and it holds itself to the rules it enforces.

## Before you start

- **Read [AGENTS.md](AGENTS.md)**, and if an AI agent writes your change, have it read it too.
  It is the source of truth: section 2 lists the rules that make a change a bug even when the
  feature works (the engine never imports a UI, the model never runs commands, only
  `verify/grade.py` writes grades, nothing touches the user's files before apply, ...).
- New features get a section in `AGENTS.md` section 15 before any code.
- New dependencies must be added to the allowed stack (section 2) first.
- Check "What not to build" in the roadmap before proposing something big: no hosted service, no
  telemetry, no accounts.

## Every change

- Comes with tests. Anything that would call a model uses `FakeModel`; no test needs a GPU or the
  network.
- Passes the full gate:

  ```bash
  uv sync
  uv run ruff check . && uv run ruff format --check .
  uv run pytest -m "not gpu"
  ```

- Updates `PROGRESS.md` (what was done, what is next, known issues) and `CHANGELOG.md`
  (Unreleased) when users would notice it.
- Uses plain English in every user-facing message: what happened, why, and what to do next.
- Is one focused commit. No drive-by refactors.

## Reporting a problem

Open an issue with the output of `qed doctor`, your QEDCode version (`qed --version`) and your OS.
Questions go to [Discussions](https://github.com/AdityaTummuri/QEDCode/discussions). Security
problems go through private vulnerability reporting, never a public issue: see
[SECURITY.md](SECURITY.md).

By contributing you agree that your contribution is licensed under the
[Apache License 2.0](LICENSE).
