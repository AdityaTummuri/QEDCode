# Using QEDCode

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

## Commands

```text
qed                                   open the terminal UI in the current folder
qed try [--solve]                     three AI changes graded (no model); --solve: a model writes the fix
qed setup [--reset]                   hardware, mode, model, downloads, helper, API keys, agents
qed solve "<task>" [--speed fast|normal|sure] [--apply] [--json] [--allow-risky]
qed check [--speed ...] [--base <ref>] [--json] [--fail-on-tampered] [--fail-below <grade>]
qed verify <receipt.json>             re-run a proof receipt: VERIFIED or what no longer matches
qed hook [install|remove] claude      run the check when Claude Code is about to stop
qed receipt [<receipt.json>] [--link] show a receipt, or make a link to it in the Receipt Viewer
qed apply [task id] [--commit]        apply a finished result (--commit: commit it with its proof)
qed undo                              take the last apply back out
qed tests [<path>]                    Safety Net Builder: write tests for code no test covers
qed hunt [<path>]                     Bug Hunter: look for bugs and prove each with a failing test
qed night [add|list|run|report]       Night Shift: queue tasks, run them unattended, read a report
qed models [list|recommend|download <id>|use <id>|remove <id>]
qed connect [agent] [--list] [--yes]  connect QEDCode to Claude Code, OpenCode, Cline, Cursor,
                                      Antigravity, VS Code
qed quiet [on|off|auto]               gentler, cooler model runs (slower)
qed sandbox [status|setup|off]        run your tests in Docker with the network off
qed memory [list|forget <#>|clear]    the fixes QEDCode remembers and shows the model as examples
qed doctor [--update-engine]          check git, Python, pytest, ruff, GPU, engine, model, disk
                                      and say how to fix each problem
qed uninstall [--all] [--yes]         remove .qed/ here (--all: also ~/.qedcode and saved keys)
qed mcp                               the MCP server over stdio (see Agents)
qed benchy [install|run|report|...]   optional: measure QEDCode against your model on its own
```

`qedcode` is the same program as `qed` (it exists so `uvx qedcode` works).

In the terminal UI: type a task and press Enter, then `a` apply, `d` diff, `e` explain, `h` try
harder, `u` undo, `r` receipt, `m` speed, `s` settings, `Esc` cancel, `?` help.

## The speed dial

| Speed | Tries | What it does |
| --- | --- | --- |
| `fast` | 1 | quick checks, no repro test; the full suite is skipped |
| `normal` (default) | up to 3 | writes a test that fails first for bugs and new features, runs all tests |
| `sure` | up to 6 | always writes a test first and aims for grade A; after a passing try, one more looks for a fix that disagrees (Smart Questions) |

QEDCode stops as soon as a try is proven, so easy tasks stay quick even at `sure`.

**Smart Questions.** When two tries pass every test but are different changes, QEDCode runs both
on generated inputs until they disagree and asks one question, for example "For
`paginate([], 0, 0)`, should it return `[]` (fix 1) or raise `ValueError` (fix 2)?". Your answer
becomes a test, and the fix that passes it is the result. Where nobody can answer (scripts, MCP),
the question is shown with the result and listed in the receipt under "not proven".

## Languages

| | Python | JavaScript / TypeScript |
| --- | --- | --- |
| Detected from | `pyproject.toml`, `pytest.ini`, tests | `package.json` with no Python project beside it |
| Tests run with | pytest in your project's Python | `node --test`, Vitest or Jest (from your `node_modules`; QEDCode never installs Node packages) |
| Edits | symbols located with `ast` | symbols located with tree-sitter (`.js .jsx .mjs .cjs .ts .tsx .mts .cts`) |
| Checks | parse, ruff, tests | parse, tests (no linter) |
| Mutation score (proof strength) | yes | no |
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

## Where things live

| Path | What |
| --- | --- |
| `<project>/.qed/` | runs, receipts, project settings (`project.toml`), memory, test copies. Excluded from git; `qed uninstall` removes it |
| `~/.qedcode/` (`%USERPROFILE%\.qedcode` on Windows) | `config.toml`, model engines, downloaded models, logs. Move it with the `QEDCODE_HOME` environment variable |
| System keychain | API keys only (never a file) |
