# QEDCode progress

## Current step
M2.7

## Done
- Setup, 2026-11-20: AGENTS.md (from the AI Build Brief), CLAUDE.md, PROGRESS.md, .gitignore, docs/ committed.
- M0.1, 2026-11-20: uv + hatchling src-layout package `qedcode` 0.0.1, `qed` Typer console script, folder skeleton, CI workflow (3 OS x py3.11/3.12), README.
- M0.2, 2026-11-20: schemas.py (section 5 models + Grade.rank), config.py (GlobalConfig/ProjectConfig, TOML load/save, QEDCODE_HOME), conftest isolates QEDCODE_HOME per test.
- M1.1, 2026-11-20: bugzoo/ with 10 projects (task.txt, buggy src, passing visible tests, failing hidden test, solution.patch, edits.txt) and check_zoo.py (10 of 10 valid); tests/integration/test_bugzoo.py.
- M1.2, 2026-11-20: runtime/client.py (ModelClient protocol), runtime/fake.py (FakeModel, YAML scripts, call recording, clear errors), tests/fixtures/scripts/001_paginate_off_by_one.yaml.
- M2.1, 2026-11-20: hardware/detect.py (NVIDIA, AMD/Intel Linux sysfs, AMD/Intel Windows registry, Apple Silicon, CPU fallback; injectable Host; overrides), 'qed doctor' hardware table, verify/run.py (the one subprocess helper, pulled forward from 3.1 because probes need it) with tests.
- M2.2, 2026-11-20: runtime/catalog.yaml (4 models, values read from the Hugging Face API), runtime/catalog.py (Pydantic validation, <verify> placeholders), hardware/recommend.py (brief's formula), 'qed models list' and 'qed models recommend' (reads vram from config), tests for 4/6/8/16 GB, Apple, CPU.
- M2.3, 2026-11-20: runtime/ollama.py (OllamaClient: chat streaming, is_running, list_models, pull, unload), runtime/openai_compat.py (OpenAICompatClient SSE), runtime/client.py (make_client factory, ModelError/ServerNotRunning, timeouts connect 5s read 600s); httpx.MockTransport tests, no real server.
- M2.4, 2026-11-20: runtime/installer.py (asset matcher for the real b12549 release names incl. Blackwell CUDA>=12.8 rule, confirm-first download, checksum from GitHub digest, safe extract, --version check, CUDA->Vulkan->CPU fallback), runtime/server_manager.py (shared llama-server: state file, lock file, client registry, free port, /health polling, idle unload, OOM hint, --help flag probe cache), runtime/llamacpp.py (LlamaCppClient, lazy start), logging_setup.py (rotation 5MB x3), spawn_detached in verify/run.py; tests use a fake llama-server script (no model).
- M2.5, 2026-11-20: installer.download_model (size shown, asks first, huggingface_hub with pinned revision, SHA-256 + size check, resumes on retry, disk-space check), remove_model, 'qed models download|use|remove' (--yes to skip prompts), best_ctx_for; huggingface_hub mocked in tests, nothing downloaded.
- M2.6, 2026-11-20: runtime/cloud.py (CloudClient over litellm with streaming, token usage counting, plain-English provider errors), runtime/secrets.py (keyring service 'qedcode' then provider env var; set_api_key stores in keyring only; mask_secrets + MaskingFilter on the app log), tests mock litellm and keyring and search config/logs/repo for a test key.

## Known issues
- LICENSE in the repo is GPL-3.0 (created with the repo); the plan's step 8.4 suggests MIT or Apache-2.0. Left as GPL-3.0 for the human to decide.
- Ruff is told to skip `*.md` and `docs/` so AGENTS.md stays byte-identical to the brief.
- HelperConfig has an extra 'base_url' field (not in the brief's TOML) so an openai_compat helper has somewhere to point; the brief's make_client(role='helper') needs it.
- PyYAML added as a dependency: the brief requires YAML (catalog.yaml, FakeModel scripts) but its allowed-stack table does not list it. Human should add it to AGENTS.md section 2.
- Ranking differs from the brief's example: 9B=1, 4B=2, Coder-7B=3, 2B=4 so bigger cards get the best model that fits; the 4B stays the default in config and wins on 4-6 GB cards. Change rank in catalog.yaml if you prefer otherwise.
- llama.cpp 'releases/latest' is v1.0.0 with NO binaries now (builds live in b12xxx tags), and Linux CUDA builds DO exist now, unlike the brief. The installer pins DEFAULT_LLAMACPP_TAG=b12549 and Linux NVIDIA gets CUDA first, then Vulkan, then CPU. Asset names (win rocm instead of hip-radeon) were taken from the real b12549 release.

## Values I must verify by hand
- Real GPU detection (RTX 5060 Ti VRAM, AMD/Intel registry path, Windows PowerShell JSON) is only covered by mocked tests; confirm on the real machines in step 8.3.
- Catalog (step 2.2): open each Hugging Face page and confirm repo, file, size, SHA-256 and pinned revision for qwen3.5-9b/4b/2b (unsloth/*-GGUF, third-party GGUFs: the official Qwen repos ship no GGUF) and qwen2.5-coder-7b (Qwen/Qwen2.5-Coder-7B-Instruct-GGUF). Values were read from the HF API on 2026-11-20; KV numbers derived from each config.json.
- llama.cpp (step 2.4): the real download+extract+start was NOT exercised in this unattended run (the auto-mode classifier blocked downloading a binary). On your PC run qed setup / the installer once: CUDA build on Windows, Vulkan on Linux, check llama-server starts with a GGUF and answers once, and that two qed doctor runs start only one server.
- qed models download qwen3.5-4b-q4km (step 2.5) was not run for real in this unattended run (no model downloads, as requested). Run it once by hand and confirm the size prompt, the progress bar and that the SHA-256 passes.

## Real-model results
-
