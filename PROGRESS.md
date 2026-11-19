# QEDCode progress

## Current step
M2.1

## Done
- Setup, 2026-11-20: AGENTS.md (from the AI Build Brief), CLAUDE.md, PROGRESS.md, .gitignore, docs/ committed.
- M0.1, 2026-11-20: uv + hatchling src-layout package `qedcode` 0.0.1, `qed` Typer console script, folder skeleton, CI workflow (3 OS x py3.11/3.12), README.
- M0.2, 2026-11-20: schemas.py (section 5 models + Grade.rank), config.py (GlobalConfig/ProjectConfig, TOML load/save, QEDCODE_HOME), conftest isolates QEDCODE_HOME per test.
- M1.1, 2026-11-20: bugzoo/ with 10 projects (task.txt, buggy src, passing visible tests, failing hidden test, solution.patch, edits.txt) and check_zoo.py (10 of 10 valid); tests/integration/test_bugzoo.py.
- M1.2, 2026-11-20: runtime/client.py (ModelClient protocol), runtime/fake.py (FakeModel, YAML scripts, call recording, clear errors), tests/fixtures/scripts/001_paginate_off_by_one.yaml.

## Known issues
- LICENSE in the repo is GPL-3.0 (created with the repo); the plan's step 8.4 suggests MIT or Apache-2.0. Left as GPL-3.0 for the human to decide.
- Ruff is told to skip `*.md` and `docs/` so AGENTS.md stays byte-identical to the brief.
- HelperConfig has an extra 'base_url' field (not in the brief's TOML) so an openai_compat helper has somewhere to point; the brief's make_client(role='helper') needs it.
- PyYAML added as a dependency: the brief requires YAML (catalog.yaml, FakeModel scripts) but its allowed-stack table does not list it. Human should add it to AGENTS.md section 2.

## Values I must verify by hand
-

## Real-model results
-
