# Troubleshooting

| Problem | What to do |
| --- | --- |
| `qed: command not found` | Open a new terminal; run `uv tool update-shell` and open another |
| Anything else odd | `qed doctor` checks everything and prints a fix for each problem |
| "pytest is not installed", or a summary that says `tests could not run` | `qed` offers to install pytest into your project's Python (it asks first). For agents and the MCP server, keep the project's environment in `.venv/` or `venv/` inside the project: they do not inherit your shell's virtualenv |
| Node tests report the runner is missing | Run `npm install` (or `pnpm`/`yarn install`) in the project yourself: QEDCode never installs Node packages |
| The model is slow | Use `qed solve --speed fast`, a smaller model (`qed models use <id>`), or check `qed doctor` for CPU-only mode |
| Out of VRAM | `qed models recommend` picks a smaller model or context; lower `ctx` under `[local]` in the config. A running model server restarts by itself when `ctx` or `kv_cache` changes |
| "Another QEDCode task is already running" but nothing is | The lock in `.qed/lock` is taken over automatically once its process is gone; if a crash left it, wait a few seconds and try again |
| `qed verify` says a receipt "is not safe to run here" | The receipt asks for a command other than this project's test command. Only re-run receipts QEDCode wrote in this project, or fix `test_command` in `.qed/project.toml` |
| A test hangs | Lower `test_timeout_seconds` in `.qed/project.toml`; `qed sandbox setup` runs Python tests with the network off |
| Something was left behind after a crash | `qed doctor` lists it; `git worktree prune` and deleting `.qed/worktrees/` is safe |
