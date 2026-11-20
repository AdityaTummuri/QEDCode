# QEDCode

QEDCode is a local-first coding engine that makes small language models (4–6 GB of VRAM) reliable.
It works on **Python** projects (pytest) and **JavaScript/TypeScript** projects (Node's built-in
`node --test`, Vitest or Jest).
Instead of trusting a model's first answer, it asks for several candidate changes, tests each one in
a safe git worktree, and returns only the best verified change with an evidence grade (A–D) and a
proof receipt that anyone can re-run with `qed verify`. **Your files are never touched until you
apply.**

QED is the Latin *quod erat demonstrandum*, "which was to be demonstrated": every fix ends with proof.

<!-- DEMO: replace this line with a short screen recording (docs/demo.gif) of `qed solve` reaching grade A. -->

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
| **C** Checked | The code parses and lints, but no test covers the change | Read the change before applying |
| **D** Not checked | No check could run, and it says why | Treat it as a suggestion |

**Quiet mode.** On a laptop, `qed quiet on` (or `auto`: only on battery) starts the model on half
the CPU threads at a low priority, pauses a few seconds between tries and unloads the model sooner.
The checks and grades are the same, it is just slower, cooler and quieter.

**It learns your project.** When you apply a fix with grade A or B, QEDCode remembers it in
`.qed/memory.db` (on your computer only). The next similar task shows the local model up to two of
those fixes as examples. Turn it off with `learn = false` under `[engine]` in the config, look at
what is remembered with `qed memory`, and forget with `qed memory forget <#>` or `qed memory clear`.
Cloud and helper models never get these examples.

Speed dial: **fast** (1 try, quick checks, grade B at best), **normal** (up to 3 tries, writes a test
first for bugs and features, runs all tests) and **sure** (up to 6 tries, always writes a test first,
aims for grade A). It stops as soon as a try reaches the target, so easy tasks stay quick.

## Install and first run

```bash
uv tool install qedcode      # or: pipx install qedcode   (once the first release is on PyPI)
qed setup                    # detects your GPU, picks a model, asks before every download
cd your-python-project
qed                          # opens the terminal UI
```

From a clone of this repository instead: `uv sync`, then prefix the commands with `uv run`.

`qed setup` finds your hardware (NVIDIA, AMD, Intel, Apple Silicon or CPU only), recommends a model
and context size that fit your VRAM, shows everything it wants to download with sizes and asks once,
then loads the model and prints a test answer with tokens per second. You can also use a model you
already run in Ollama or LM Studio, or a cloud model (your API key goes into your system keychain,
never into a file).

## Languages

| | Python | JavaScript / TypeScript |
| --- | --- | --- |
| Detected from | `pyproject.toml`, `pytest.ini`, tests | `package.json` with no Python project beside it |
| Tests run with | pytest in your project's Python | `node --test`, Vitest or Jest (from your `node_modules`; QEDCode never installs Node packages) |
| Edits | symbols located with `ast` | symbols located with tree-sitter (`.js .jsx .mjs .cjs .ts .tsx .mts .cts`) |
| Checks | parse, ruff, tests | parse, tests (no linter yet) |
| Sealed test box (Docker) | yes | not yet |

For TypeScript with the built-in runner you need Node 22.18 or newer; Vitest and Jest work on any
supported Node (20+).

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
