# Maintaining QEDCode

The goal: the project keeps working for six months untouched and needs about 30 minutes a month
when you look. Each item below is written as a prompt you can hand to an AI agent as it is (have
it read `AGENTS.md` first).

## What runs by itself

| What | Where |
| --- | --- |
| Tests on Linux, macOS and Windows, Python 3.11 and 3.12, on every push and pull request | `.github/workflows/ci.yml` |
| A weekly run against the newest allowed dependencies (lock file untouched) | `ci.yml`, job `newest-dependencies` (Mondays) |
| One grouped dependency pull request a week, for Python and for GitHub Actions | `.github/dependabot.yml` |
| One tag does a release: PyPI, GitHub Release notes, MCP Registry, fresh README stats and screenshots | `.github/workflows/release.yml` |
| The website rebuilds on every change to it and on every release | `.github/workflows/pages.yml` |
| QEDCode grades its own pull requests | `.github/workflows/qedcode-check.yml` |

Nothing that can go stale is kept by hand: the README stats, the screenshots, the receipt
schema, the game's levels and the sample receipt are all generated.

## The monthly routine (30 minutes)

1. **Dependencies.**
   > Look at the open Dependabot pull requests on AdityaTummuri/QEDCode. For each one whose CI is
   > green, merge it. For a red one, read the failing job, and either fix the code in a separate
   > commit with a test, or pin the dependency below the breaking version in `pyproject.toml` and
   > say why in the commit message.

2. **Issues.**
   > Triage the new issues. Reproduce each bug from its `qed doctor` output on a bug zoo copy.
   > Label it, answer it, or close it with a link to "What not to build" in the roadmap when it
   > asks for something out of scope. A wrong grade A is always a bug: write a failing test first.

3. **Weekly runs.**
   > Check the last four weekly `newest-dependencies` runs of QEDCode, QEDCode-benchy and
   > QEDCode-AgentSkill. Open an issue for any that failed, with the failing step and the
   > dependency that changed.

4. **Each quarter, also:**
   > Bump `DEFAULT_LLAMACPP_TAG` to the newest llama.cpp build that has release assets for every
   > platform, re-verify every entry in `src/qedcode/runtime/catalog.yaml` against the Hugging Face
   > API (size, SHA-256, pinned revision), and rerun Benchy's smoke run
   > (`qed benchy run --suite benchy-core --reps 3`) to catch quality drift. Record the result in
   > PROGRESS.md.

## Releasing

> Move the CHANGELOG's Unreleased section under `## [X.Y.Z] - <date>`, set the same version in
> `pyproject.toml`, `src/qedcode/__init__.py`, `server.json` (both places) and the packaging test,
> run the full gate, commit, then `git tag vX.Y.Z && git push origin main vX.Y.Z`. Watch the
> Release and Website workflows finish.

## Known expiry dates

- **Material for MkDocs reaches end of life on 2027-05-05.** Its successor is Zensical, which reads
  the same `mkdocs.yml`; move the docs build to it before then.
- **llama.cpp asset names** change now and then; the installer pins a tag, so this only matters
  at the quarterly bump.
- **The MCP Registry** is in preview; if `mcp-publisher` changes its flow, update the
  `mcp-registry` job and `MCP_PUBLISHER_VERSION` together.
