# Install

## Try it first (no install, no GPU)

```bash
uvx qedcode try        # three AI changes to one bug, graded; a few seconds
uvx qedcode check      # grade the uncommitted changes in your own repository (no model needed)
```

`uvx` comes with [uv](https://docs.astral.sh/uv/) and runs QEDCode without installing it. Install it
for good when you want `qed` on your PATH.

## What you need

| | Required | Notes |
| --- | --- | --- |
| Git | yes | QEDCode works in git worktrees and needs a repository with at least one commit (it offers to set one up) |
| Python 3.11+ | yes | `uv` installs and manages it for you |
| A project with tests | recommended | Python (pytest) or JavaScript/TypeScript (`node --test`, Vitest or Jest). Without tests the best grade is C |
| Node.js 20+ | JavaScript/TypeScript projects | 22.18+ to run `.ts` tests with the built-in runner |
| A GPU | recommended for `qed solve` | NVIDIA (CUDA), AMD or Intel (Vulkan), Apple Silicon (Metal). CPU-only works but is slow. `qed check` and `qed verify` need no model at all |
| Docker | optional | only for the sealed test box (`qed sandbox setup`) |
| Disk space | 3–6 GB | the model; QEDCode shows the exact size and asks before every download |

## Install the `qed` command

QEDCode is installed as a command-line tool with [uv](https://docs.astral.sh/uv/) (or pipx):

```bash
uv tool install qedcode        # or: pipx install qedcode
qed --version
```

The default install (about 70 MB) covers `qed check`, `qed verify`, `qed try`, `qed hook` and
`qed solve` through Ollama or an OpenAI-compatible server. Three optional parts are added by name:

| Extra | Adds | Install |
| --- | --- | --- |
| `tui` | the terminal UI (`qed` with no arguments) | `uv tool install "qedcode[tui]"` |
| `download` | downloading models for llama.cpp | `uv tool install "qedcode[download]"` |
| `cloud` | cloud models through LiteLLM (about 230 MB) | `uv tool install "qedcode[cloud]"` |
| `all` | all three | `uv tool install "qedcode[all]"` |

Not on PyPI yet where you are, or want the newest commit? Install straight from GitHub:
`uv tool install git+https://github.com/AdityaTummuri/QEDCode`.

### macOS

```bash
# 1. git (skip if `git --version` already works)
xcode-select --install
# 2. uv
brew install uv                                  # or: curl -LsSf https://astral.sh/uv/install.sh | sh
# 3. QEDCode
uv tool install qedcode
qed --version
```

Apple Silicon Macs use the Metal build of the model engine automatically. API keys are stored in the
macOS Keychain.

### Linux

```bash
# 1. git and curl (Debian/Ubuntu shown; use your package manager)
sudo apt install git curl
# 2. uv
curl -LsSf https://astral.sh/uv/install.sh | sh        # then open a new terminal
# 3. QEDCode
uv tool install qedcode
qed --version
```

GPU drivers: NVIDIA needs the proprietary driver (`nvidia-smi` must work); AMD and Intel use the
Vulkan build, so install your distribution's Vulkan driver (for example `mesa-vulkan-drivers`).
API keys go into the system keyring (Secret Service, for example GNOME Keyring or KWallet); on a
server without one, set the provider's usual environment variable instead (for example
`OPENAI_API_KEY`).

### Windows

Use PowerShell (Windows Terminal recommended).

```powershell
# 1. git (skip if `git --version` already works)
winget install --id Git.Git -e
# 2. uv
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# 3. open a NEW PowerShell window, then install QEDCode
uv tool install qedcode
qed --version
```

NVIDIA GPUs use the CUDA build (keep the driver up to date); AMD and Intel GPUs use the Vulkan
build. API keys go into Windows Credential Manager. For JavaScript/TypeScript projects QEDCode links
your `node_modules` into its test copies; if Windows refuses to create the link, turn on Developer
Mode (Settings > For developers).

### Update or remove

```bash
uv tool upgrade qedcode          # update
qed uninstall --all              # remove .qed/ here, ~/.qedcode (engines, models, logs, keys) ...
uv tool uninstall qedcode        # ... then the program itself
```

### From a clone (for developers)

```bash
git clone https://github.com/AdityaTummuri/QEDCode && cd QEDCode
uv sync
uv run qed --version
```

## First run

```bash
qed setup
```

`qed setup` finds your hardware, recommends a model and context size that fit your VRAM, lists
everything it wants to download with sizes, and asks once. Then it loads the model and prints a
test answer with tokens per second. It also lets you:

- use a model you already run in **Ollama** or **LM Studio** instead of downloading one,
- pick a **cloud** model (your API key goes into the system keychain, never into a file),
- set up a **helper** model for hard tasks, and
- connect QEDCode to your coding agents (Claude Code, OpenCode, Cline, Cursor, Antigravity,
  VS Code).

Run `qed doctor` at any time: it checks git, your project's Python or Node, the test runner, ruff,
your GPU, the model engine, the model, the model server and free disk space, and tells you how to
fix whatever is wrong.

## Tested on

Machines QEDCode has been run on by hand with a real model. The automated tests run on Linux,
macOS and Windows with Python 3.11 and 3.12 on every push, with a scripted stand-in model.

| Machine | GPU | OS | Model | Result |
| --- | --- | --- | --- | --- |
| Laptop, Ryzen 7 7840HS, 14 GB RAM | NVIDIA RTX 4050 Laptop, 6 GB | Ubuntu 24.04 | qwen3.5:4b and qwen2.5-coder:7b via Ollama | `qed try --real`: grade A in 15-43 s; Benchy runs in the [results](results.md) (2026-11-23) |
| MacBook, Apple M2 | Apple GPU (Metal) | macOS | scripted model (development machine) | the full test suite |
