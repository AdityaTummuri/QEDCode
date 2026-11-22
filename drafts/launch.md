# Launch week

Aim: Tuesday 2027-01-12, after the holidays, once Phases 1-4 are live (numbers, `qed try`, the
website, the action). Answer every comment on day one; fold the questions into the FAQ.

| Day | Where | What |
| --- | --- | --- |
| Tue | Show HN | "QEDCode: grade AI code changes A-D with a re-runnable proof" (link the website; first comment: what it is, the four numbers, what it does not do) |
| Tue | r/LocalLLaMA | lead with the 4B-vs-9B result and the 6 GB laptop |
| Wed | r/Python | Fool the Grader and the receipt format |
| Wed | dev.to + the site | post 1 (the agent that googled the answers) |
| Thu | LinkedIn | the video |
| Thu | X / Bluesky | a thread with the GIF and the Receipt Viewer link |
| Fri | MCP directories, awesome lists | one-time listings |

## Answers to have ready

- **"Why not just use Claude Code?"** Use it. QEDCode is a tool those agents call (MCP, the
  action, `qed check`), not a rival chat agent.
- **"Tests written by the same model?"** They must fail on the old code first, existing tests are
  protected, mutation testing measures them, and Benchy checks grade A against hidden tests.
- **"Is it slow?"** It stops at the first proven try; `qed check` takes about as long as your tests.
- **"Does my code leave my machine?"** Not in local mode; there is no telemetry and no account.
