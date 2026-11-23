# FAQ

**Why not just use Claude Code (or Cursor, or Copilot)?**
Use them. QEDCode is not a rival chat agent; it is a tool those agents call. They write the change,
QEDCode proves it: `qed check` grades their work from evidence, and the MCP server lets them ask
QEDCode for a verified fix. The [Start here](start-here.md) page shows the two-command version.

**Is QEDCode an AI model?**
No. It is a manager program. It uses existing models (Qwen by default, or any cloud model) and
checks everything they write with deterministic tools: the parser, ruff, your tests and a test that
must fail before the change.

**What does a grade actually prove?**
Grade A means a test failed on the old code and passes on the new code, the full suite shows no
regressions, nothing protected changed and no risky code needed approval. Each receipt also lists
what is *not* proven (for example "tests ran without network blocking"), and the mutation score
says how strongly the new test pins the behaviour.

**Can a test written by the same model be trusted?**
Not blindly, which is why the grade needs the test to fail first on the old code, why existing
tests are protected, and why mutation testing checks that the test notices small breakages of the
changed lines. Benchy measures how often an A is wrong against hidden tests the model never saw.

**Can a small model beat a big one?**
Not in general knowledge. It can be more reliable on tasks that have tests, because every answer is
tested before you see it. Whether 4B + QEDCode matches 9B alone is one of the
[measured numbers](results.md).

**Is it slower than other tools?**
A bit, because it tests every try. It stops as soon as a try is proven, so only hard tasks take
longer. A fast wrong answer costs you more time later.

**Does it change my code without asking?**
Never. It works in a safe copy (a git worktree). Your files change only when you apply, and undo
takes the change back out.

**Does my code leave my computer?**
Not in local mode. In mix or cloud mode, the parts of your code sent to the helper or cloud model
leave your computer, and QEDCode says so before it happens. There is no telemetry.

**Do I need a GPU?**
Not for `qed check`, `qed verify`, `qed try`, `qed hook` or the GitHub Action. For `qed solve` a GPU with
4–6 GB is the target; CPU-only works for small tasks, or use a cloud model.

**My project has no tests. Will it still help?**
Yes, but less. QEDCode writes a test for your task (normal and sure speeds), and `qed tests` builds
a safety net for code no test covers. When nothing can be tested the grade is C, so you know to
read the change yourself.

**Which languages work?**
Python (pytest) and JavaScript/TypeScript (`node --test`, Vitest, Jest). Others wait until people
ask for them.

**Why is there no hosted version?**
Running other people's code on a server costs money, attracts abuse and needs uptime. Everything
QEDCode offers runs on a machine you already have: your computer, your browser, or your CI.
