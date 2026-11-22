# Use QEDCode from your coding agent

QEDCode is also an MCP server, so Claude Code, OpenCode, Cline, Cursor, Antigravity, VS Code and
other agents can call it as a tool. They get the fix, its grade and the proof receipt, and the model server is
shared with the terminal UI (only one `llama-server` runs).

```bash
qed connect                              # finds your agents, shows what changes, asks first
claude mcp add qedcode -- qed mcp        # the same thing for Claude Code by hand
```

`qed connect` (and the last step of `qed setup`) handles six agents: it runs `claude mcp add`
for Claude Code, `opencode mcp add` for OpenCode and `code --add-mcp` for VS Code, and merges a
`qedcode` entry into Cline's CLI settings file, Cursor's `~/.cursor/mcp.json` and Antigravity's
`~/.gemini/config/mcp_config.json` (through `agy mcp add` when the Antigravity CLI is installed).
A backup copy is made first, and a file that is not plain JSON is never touched: you get the
snippet instead. `qed connect --list` shows what is connected.

For other agents add an MCP server that runs the command `qed` with the argument `mcp`. Their config
formats change, so check each tool's docs:

| Agent | Docs | Snippet |
| --- | --- | --- |
| OpenCode | https://opencode.ai/docs/mcp-servers | `{"mcp": {"servers": {"qedcode": {"type": "local", "command": ["qed", "mcp"]}}}}` |
| Cline | https://docs.cline.bot/mcp/mcp-overview | `{"mcpServers": {"qedcode": {"command": "qed", "args": ["mcp"]}}}` |
| Cursor | https://cursor.com/docs (search for "MCP") | `{"mcpServers": {"qedcode": {"command": "qed", "args": ["mcp"]}}}` |
| Antigravity | https://antigravity.google/docs/mcp | `{"mcpServers": {"qedcode": {"command": "qed", "args": ["mcp"]}}}` in `~/.gemini/config/mcp_config.json` (or `<workspace>/.agents/mcp_config.json`) |
| VS Code | https://code.visualstudio.com/docs/agent-customization/mcp-servers | `{"servers": {"qedcode": {"type": "stdio", "command": "qed", "args": ["mcp"]}}}` in `.vscode/mcp.json` |

Then ask your agent, for example: *"Use the qedcode tool to fix the bug described in task.txt"* or
*"I changed the code; check it with qedcode"*. The tools:

| Tool | Arguments | Returns |
| --- | --- | --- |
| `solve` | `task`, `speed="normal"`, `apply=false`, `wait_seconds=600`, `project_dir` | `status` (done, running, no_fix, needs_input, busy), `job_id`, `grade`, `summary`, `explanation`, `diff`, `receipt_path`, `question` |
| `check` | `speed="normal"`, `base="HEAD"`, `project_dir` | `grade`, `summary`, `failing_tests`, `receipt_path`, `notes` |
| `verify` | `receipt_path` | `verified`, `mismatches` |
| `status` | `job_id` | progress lines so far, or the final result |

Solves take minutes. If `solve` returns `status: "running"`, the agent polls `status` with the
`job_id`; if an agent gives up on slow tools, raise its MCP tool timeout (in Claude Code, the
`MCP_TOOL_TIMEOUT` setting; check its docs). One job runs per project at a time (a second call gets
`status: "busy"`), and agent runs never answer questions, never approve risky code and never use the
helper model unless your config pre-approves it (`[helper] ask_before_use = false`).

## In Antigravity or VS Code, with a cloud model

You don't need a GPU to use QEDCode from an IDE. Your IDE's agent runs on its own cloud model, and
QEDCode's tries can run on a cloud model too:

```bash
uv tool install qedcode
qed setup                    # choose "cloud", pick a provider and model, paste the API key
qed connect antigravity      # or: qed connect vscode
```

Restart the IDE's agent (in Antigravity: agent panel → **…** → MCP Servers → refresh; in VS Code:
open the chat in Agent mode and check the tools list). Then ask: *"Fix the bug in task.txt and
prove it with the qedcode tools"*. `check` and `verify` need no model at all; only `solve` uses the
cloud model, and your code is sent to that provider (cloud mode says so in `qed setup`).

Keep the key in the system keychain, where `qed setup` puts it. An IDE started from the Dock or
the Start menu doesn't see the environment variables of your shell, and an API key should never
go into an MCP config file. To teach the IDE's agent when and how to use QEDCode, add the
[QEDCode Agent Skill](https://github.com/AdityaTummuri/QEDCode-AgentSkill).

## The QEDCode Agent Skill

The [Agent Skill](https://github.com/AdityaTummuri/QEDCode-AgentSkill) teaches an agent *when* to
call QEDCode: check its own change before it says "done", and never weaken a test to get a pass.
In its first test, Antigravity reached grade A on 4 of 4 bug zoo projects from prompts that never
named QEDCode.

## In a pull request (GitHub Action)

The [QEDCode Check action](github-action.md) runs `qed check` on every pull request, so a change an
agent wrote gets a grade and a receipt before anyone reviews it.
