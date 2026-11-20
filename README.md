# QEDCode

**A local-first coding engine that makes small language models reliable.**

Small models (4–6 GB of VRAM) are good at writing code and bad at being right the first time.
QEDCode does not trust a first answer. It asks for several candidate changes, tests each one in a
safe copy of your project, and gives you only the best verified change, with an evidence grade
(A–D), a plain-English explanation and a **proof receipt** that anyone can re-run with `qed verify`.
**Your files are never touched until you apply a result.** Everything runs on your computer; cloud
models are optional and only used after you agree.

QED is the Latin *quod erat demonstrandum*, "which was to be demonstrated": every fix ends with proof.

<!-- DEMO: replace this line with a short screen recording (docs/demo.gif) of `qed solve` reaching grade A. -->

> **Status.** The engine, terminal UI, CLI and MCP server are complete and covered by more than
> 1,100 automated tests that use a scripted stand-in model. Checks that need real hardware (GPUs,
> real model quality, Docker, Windows and Linux machines) are listed in `PROGRESS.md`; please run
> `qed doctor` and report anything it cannot fix.

## Contents

- [What you need](#what-you-need) · [Install](#install) · [First run](#first-run) · [A first task](#a-first-task)
- [How it works](#how-it-works) · [Languages](#languages) · [Models and modes](#models-and-modes)
- [Commands](#commands) · [Use with agents](#use-with-agents) · [Safety](#safety)
- [Where things live](#where-things-live) · [Troubleshooting](#troubleshooting) · [Develop](#develop)

## What you need

| | Required | Notes |
| --- | --- | --- |
| Git | yes | QEDCode works in git worktrees and needs a repository with at least one commit (it offers to set one up) |
| Python 3.11+ | yes | `uv` installs and manages it for you |
| A project with tests | recommended | Python (pytest) or JavaScript/TypeScript (`node --test`, Vitest or Jest). Without tests the best grade is C |
| Node.js 20+ | JavaScript/TypeScript projects | 22.18+ to run `.ts` tests with the built-in runner |
| A GPU | recommended | NVIDIA (CUDA), AMD or Intel (Vulkan), Apple Silicon (Metal). CPU-only works but is slow |
| Docker | optional | only for the sealed test box (`qed sandbox setup`) |
| Disk space | 3–6 GB | the model; QEDCode shows the exact size and asks before every download |

## Install

QEDCode is installed as a command-line tool with [uv](https://docs.astral.sh/uv/). Pick your system.
(Once the first release is on PyPI, `uv tool install qedcode` or `pipx install qedcode` will work
too; until then install straight from GitHub as shown.)

### macOS

```bash
# 1. git (skip if `git --version` already works)
xcode-select --install
# 2. uv
brew install uv                                  # or: curl -LsSf https://astral.sh/uv/install.sh | sh
# 3. QEDCode
uv tool install git+https://github.com/AdityaTummuri/QEDCode
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
uv tool install git+https://github.com/AdityaTummuri/QEDCode
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
uv tool install git+https://github.com/AdityaTummuri/QEDCode
qed --version
```

NVIDIA GPUs use the CUDA build (keep the driver up to date); AMD and Intel GPUs use the Vulkan
build. API keys go into Windows Credential Manager. For JavaScript/TypeScript projects QEDCode links
your `node_modules` into its test copies; if Windows refuses to create the link, turn on Developer
Mode (Settings > For developers).

### Update or remove

```bash
uv tool upgrade qedcode          # update (for the GitHub install: uv tool install --force git+https://github.com/AdityaTummuri/QEDCode)
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
- connect QEDCode to your coding agents (Claude Code, OpenCode, Cline, Cursor).

Run `qed doctor` at any time: it checks git, your project's Python or Node, the test runner, ruff,
your GPU, the model engine, the model, the model server and free disk space, and tells you how to
fix whatever is wrong.

## A first task

```bash
cd your-project            # a git repository with a first commit
qed                        # opens the terminal UI (it confirms what it detected, once)
```

Type what you want ("fix the bug where the last item of a page is dropped") and press Enter. Watch
the tries, then press `d` to see the diff, `e` for the explanation, `a` to apply and `u` to undo.

Or from a script or terminal:

```bash
qed solve "paginate drops the last item" --speed normal      # nothing is applied
qed apply                                                    # apply the result (asks if the grade is low)
qed undo                                                     # take it back out
qed check                                                    # grade changes you made yourself
qed verify .qed/receipts/<task id>.json                      # re-run a proof receipt
```

## How it works

1. It takes a snapshot of your project (including uncommitted files) and works in copies of it.
2. A small model writes a test that shows the problem (it must fail on your current code).
3. The model proposes up to 3 changes (6 on `sure`); each one is checked by your own tests and by
   deterministic guards, cheapest check first. The first failing check rejects it.
4. Near misses are repaired from the error message; in `mix` mode a stronger helper model can be
   asked (only after you agree).
5. The best candidate gets a grade, a plain-English explanation and a receipt.

| Grade | Meaning | What to do |
| --- | --- | --- |
| **A** Proven | A test failed before, passes now, the full suite shows no regressions, nothing protected changed | Safe to apply |
| **B** Tested | The tests pass, but nothing proved the exact problem | Apply, then take a quick look |
| **C** Checked | The code parses (and lints for Python), but no test covers the change | Read the change before applying |
| **D** Not checked | No check could run, and it says why | Treat it as a suggestion |

Speed dial: **fast** (1 try, quick checks, grade B at best), **normal** (up to 3 tries, writes a test
first for bugs and features, runs all tests) and **sure** (up to 6 tries, always writes a test first,
aims for grade A). It stops as soon as a try reaches the target, so easy tasks stay quick.

Two options make it kinder to your laptop and smarter over time:

- **Quiet mode**: `qed quiet on` (or `auto`: only on battery) starts the model on half the CPU
  threads at a low priority, pauses a few seconds between tries and unloads the model sooner. The
  checks and grades are the same; it is just slower, cooler and quieter.
- **It learns your project**: when you apply a fix with grade A or B, QEDCode remembers it in
  `.qed/memory.db` (on your computer only) and shows the local model up to two similar fixes as
  examples next time. See them with `qed memory`, forget with `qed memory forget <#>` or
  `qed memory clear`, turn it off with `learn = false` under `[engine]` in the config. Cloud and
  helper models never get these examples.

## Languages

| | Python | JavaScript / TypeScript |
| --- | --- | --- |
| Detected from | `pyproject.toml`, `pytest.ini`, tests | `package.json` with no Python project beside it |
| Tests run with | pytest in your project's Python | `node --test`, Vitest or Jest (from your `node_modules`; QEDCode never installs Node packages) |
| Edits | symbols located with `ast` | symbols located with tree-sitter (`.js .jsx .mjs .cjs .ts .tsx .mts .cts`) |
| Checks | parse, ruff, tests | parse, tests (no linter) |
| Sealed test box (Docker) | yes | no |

## Models and modes

| Mode | What runs where |
| --- | --- |
| **local** (default) | everything on your computer, nothing is sent out |
| **mix** | local model first; a stronger helper model is asked only when the local one is stuck and you agree (parts of your code are sent to it) |
| **cloud** | a cloud model does the work (through LiteLLM; OpenRouter, Anthropic, OpenAI, Gemini or any provider string) |

Local models: the built-in catalog (`qed models list`) has Qwen3.5 9B/4B/2B and Qwen2.5-Coder 7B,
chosen to fit your VRAM (`qed models recommend`). QEDCode runs them with llama.cpp (one shared server
per computer), or uses Ollama or any OpenAI-compatible server such as LM Studio.

## Commands

```text
qed                                   open the terminal UI in the current folder
qed setup [--reset]                   hardware, mode, model, downloads, helper, API keys, agents
qed solve "<task>" [--speed fast|normal|sure] [--apply] [--json] [--allow-risky]
qed check [--speed ...] [--base <ref>] [--json]    grade the changes you made yourself
qed verify <receipt.json>             re-run a proof receipt: VERIFIED or what no longer matches
qed apply [task id] / qed undo        apply a finished result / take the last apply back out
qed models [list|recommend|download <id>|use <id>|remove <id>]
qed connect [agent] [--list] [--yes]  connect QEDCode to Claude Code, OpenCode, Cline, Cursor
qed quiet [on|off|auto]               gentler, cooler model runs (slower)
qed sandbox [status|setup|off]        run your tests in Docker with the network off
qed memory [list|forget <#>|clear]    the fixes QEDCode remembers and shows the model as examples
qed doctor [--update-engine]          check git, Python, pytest, ruff, GPU, engine, model, disk
                                      and say how to fix each problem
qed uninstall [--all] [--yes]         remove .qed/ here (--all: also ~/.qedcode and saved keys)
qed mcp                               the MCP server over stdio (see below)
```

In the terminal UI: type a task and press Enter, then `a` apply, `d` diff, `e` explain, `h` try
harder, `u` undo, `r` receipt, `m` speed, `s` settings, `Esc` cancel, `?` help.

## Use with agents

QEDCode is also an MCP server, so Claude Code, OpenCode, Cline, Cursor and other agents can call it
as a tool. They get the fix, its grade and the proof receipt, and the model server is shared with the
terminal UI (only one `llama-server` runs).

```bash
qed connect                              # finds your agents, shows what changes, asks first
claude mcp add qedcode -- qed mcp        # the same thing for Claude Code by hand
```

`qed connect` (and the last step of `qed setup`) handles four agents: it runs `claude mcp add`
for Claude Code, runs `opencode mcp add` for OpenCode, and merges a `qedcode` entry into Cline's CLI
settings file and Cursor's `~/.cursor/mcp.json` (with a backup copy; a file that is not plain JSON
is never touched, you get the snippet instead). `qed connect --list` shows what is connected.

For other agents add an MCP server that runs the command `qed` with the argument `mcp`. Their config
formats change, so check each tool's docs:

| Agent | Docs | Snippet |
| --- | --- | --- |
| OpenCode | https://opencode.ai/docs/mcp-servers | `{"mcp": {"servers": {"qedcode": {"type": "local", "command": ["qed", "mcp"]}}}}` |
| Cline | https://docs.cline.bot/mcp/mcp-overview | `{"mcpServers": {"qedcode": {"command": "qed", "args": ["mcp"]}}}` |
| Cursor | https://cursor.com/docs (search for "MCP") | `{"mcpServers": {"qedcode": {"command": "qed", "args": ["mcp"]}}}` |

Then ask your agent, for example: *"Use the qedcode tool to fix the bug described in task.txt"* or
*"I changed the code; check it with qedcode"*. The tools:

| Tool | Arguments | Returns |
| --- | --- | --- |
| `solve` | `task`, `speed="normal"`, `apply=false`, `wait_seconds=600`, `project_dir` | `status` (done, running, no_fix, needs_input, busy), `job_id`, `grade`, `summary`, `explanation`, `diff`, `receipt_path`, `question` |
| `check` | `speed="normal"`, `base="HEAD"`, `project_dir` | `grade`, `summary`, `failing_tests`, `receipt_path` |
| `verify` | `receipt_path` | `verified`, `mismatches` |
| `status` | `job_id` | progress lines so far, or the final result |

Solves take minutes. If `solve` returns `status: "running"`, the agent polls `status` with the
`job_id`; if an agent gives up on slow tools, raise its MCP tool timeout (in Claude Code, the
`MCP_TOOL_TIMEOUT` setting; check its docs). One job runs per project at a time (a second call gets
`status: "busy"`), and agent runs never answer questions, never approve risky code and never use the
helper model unless your config pre-approves it (`[helper] ask_before_use = false`).

## Safety

- Work happens in git worktrees under `.qed/worktrees/`; your working tree is written only by
  `qed apply` / `qed undo`, which refuse if a touched file changed since the snapshot.
- The model never runs commands. Only fixed commands built by QEDCode run: git, your project's
  Python with pytest, ruff, Node with your test runner, and the llama.cpp server.
- Existing tests and test configuration are protected: a candidate that edits, deletes, skips or
  weakens them is rejected. A candidate that adds risky code (`subprocess`, network, `eval`, ...)
  needs your approval and can then reach grade B at most.
- Every download is announced with its size and asked first; API keys live only in the system
  keychain or environment variables; project commands run with secret-looking variables removed.
- By default tests run on your computer, without network blocking or a container, and every
  receipt says so under "not proven". `qed sandbox setup` (needs Docker) builds a **sealed test
  box**: all tests then run in a container with the network off, and the receipt says so.

## Where things live

| Path | What |
| --- | --- |
| `<project>/.qed/` | runs, receipts, project settings (`project.toml`), memory, test copies. Excluded from git; `qed uninstall` removes it |
| `~/.qedcode/` (`%USERPROFILE%\.qedcode` on Windows) | `config.toml`, model engines, downloaded models, logs. Move it with the `QEDCODE_HOME` environment variable |
| System keychain | API keys only (never a file) |

## Troubleshooting

| Problem | What to do |
| --- | --- |
| `qed: command not found` | Open a new terminal; run `uv tool update-shell` and open another |
| Anything else odd | `qed doctor` checks everything and prints a fix for each problem |
| "pytest is not installed" | `qed` offers to install it into your project's Python (it asks first) |
| Node tests report the runner is missing | Run `npm install` (or `pnpm`/`yarn install`) in the project yourself: QEDCode never installs Node packages |
| The model is slow | Use `qed solve --speed fast`, a smaller model (`qed models use <id>`), or check `qed doctor` for CPU-only mode |
| Out of VRAM | `qed models recommend` picks a smaller model or context; lower `ctx` under `[local]` in the config |
| A test hangs | Lower `test_timeout_seconds` in `.qed/project.toml`; `qed sandbox setup` runs Python tests with the network off |
| Something was left behind after a crash | `qed doctor` lists it; `git worktree prune` and deleting `.qed/worktrees/` is safe |

## Develop

```bash
uv sync
uv run pytest -m "not gpu"      # no GPU, no network, no real model: a scripted FakeModel stands in
uv run ruff check . && uv run ruff format --check .
uv run python bugzoo/check_zoo.py        # the 10 tiny buggy projects used to test the engine
uv run python bugzoo/run_zoo.py --speed normal   # real-model numbers (needs `qed setup` first)
```

The build rules live in `AGENTS.md`; `PROGRESS.md` records what is done, what was verified by
hand and what still needs a real GPU.
