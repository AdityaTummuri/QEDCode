# QEDCode progress

## Current step
M2.4

## Done
- Setup, 2026-11-20: AGENTS.md (from the AI Build Brief), CLAUDE.md, PROGRESS.md, .gitignore, docs/ committed.
- M0.1, 2026-11-20: uv + hatchling src-layout package `qedcode` 0.0.1, `qed` Typer console script, folder skeleton, CI workflow (3 OS x py3.11/3.12), README.
- M0.2, 2026-11-20: schemas.py (section 5 models + Grade.rank), config.py (GlobalConfig/ProjectConfig, TOML load/save, QEDCODE_HOME), conftest isolates QEDCODE_HOME per test.
- M1.1, 2026-11-20: bugzoo/ with 10 projects (task.txt, buggy src, passing visible tests, failing hidden test, solution.patch, edits.txt) and check_zoo.py (10 of 10 valid); tests/integration/test_bugzoo.py.
- M1.2, 2026-11-20: runtime/client.py (ModelClient protocol), runtime/fake.py (FakeModel, YAML scripts, call recording, clear errors), tests/fixtures/scripts/001_paginate_off_by_one.yaml.
- M2.1, 2026-11-20: hardware/detect.py (NVIDIA, AMD/Intel Linux sysfs, AMD/Intel Windows registry, Apple Silicon, CPU fallback; injectable Host; overrides), 'qed doctor' hardware table, verify/run.py (the one subprocess helper, pulled forward from 3.1 because probes need it) with tests.
- M2.2, 2026-11-20: runtime/catalog.yaml (4 models, values read from the Hugging Face API), runtime/catalog.py (Pydantic validation, <verify> placeholders), hardware/recommend.py (brief's formula), 'qed models list' and 'qed models recommend' (reads vram from config), tests for 4/6/8/16 GB, Apple, CPU.
- M2.3, 2026-11-20: runtime/ollama.py (OllamaClient: chat streaming, is_running, list_models, pull, unload), runtime/openai_compat.py (OpenAICompatClient SSE), runtime/client.py (make_client factory, ModelError/ServerNotRunning, timeouts connect 5s read 600s); httpx.MockTransport tests, no real server.

## Known issues
- LICENSE in the repo is GPL-3.0 (created with the repo); the plan's step 8.4 suggests MIT or Apache-2.0. Left as GPL-3.0 for the human to decide.
- Ruff is told to skip `*.md` and `docs/` so AGENTS.md stays byte-identical to the brief.
- HelperConfig has an extra 'base_url' field (not in the brief's TOML) so an openai_compat helper has somewhere to point; the brief's make_client(role='helper') needs it.
- PyYAML added as a dependency: the brief requires YAML (catalog.yaml, FakeModel scripts) but its allowed-stack table does not list it. Human should add it to AGENTS.md section 2.
- Ranking differs from the brief's example: 9B=1, 4B=2, Coder-7B=3, 2B=4 so bigger cards get the best model that fits; the 4B stays the default in config and wins on 4-6 GB cards. Change rank in catalog.yaml if you prefer otherwise.

## Values I must verify by hand
- Real GPU detection (RTX 5060 Ti VRAM, AMD/Intel registry path, Windows PowerShell JSON) is only covered by mocked tests; confirm on the real machines in step 8.3.
- Catalog (step 2.2): open each Hugging Face page and confirm repo, file, size, SHA-256 and pinned revision for qwen3.5-9b/4b/2b (unsloth/*-GGUF, third-party GGUFs: the official Qwen repos ship no GGUF) and qwen2.5-coder-7b (Qwen/Qwen2.5-Coder-7B-Instruct-GGUF). Values were read from the HF API on 2026-11-20; KV numbers derived from each config.json.

## Real-model results
-
