# Built by AI, proven by tests

QEDCode is a tool that refuses to trust AI-written code. It was itself written by AI agents,
directed by one person, under the same discipline it enforces: a written brief, a progress log,
a "done when" check for every step, and tests for everything.

## The discipline

| Practice | Where it lives |
| --- | --- |
| A build brief that is the source of truth, read before any code | [`AGENTS.md`](https://github.com/AdityaTummuri/QEDCode/blob/main/AGENTS.md) (about 80 KB) |
| Non-negotiable rules, where breaking one is a bug even if the feature works | `AGENTS.md` section 2 (engine purity, the model never runs commands, no success without evidence, ...) |
| A step-by-step plan where each step ends with "Done when" | the build plan in `docs/` |
| A log of what was done, what is next and what still needs a human | [`PROGRESS.md`](https://github.com/AdityaTummuri/QEDCode/blob/main/PROGRESS.md) |
| A spec section before any post-v1 feature gets code | `AGENTS.md` section 15 |
| Tests with no GPU, network or real model: a scripted `FakeModel` stands in | `tests/`, run on Linux, macOS and Windows with Python 3.11 and 3.12 |
| CI that checks the architecture, not only the behaviour | a grep in CI fails the build if an engine module imports a UI module |

An excerpt from the brief's rules:

> **3. No success without evidence.** Every result returned to any UI carries a grade computed
> only in `verify/grade.py` and a `Receipt`. No other code path may set or upgrade a grade.
>
> **2. The model never runs commands.** Only fixed commands built by QEDCode code run [...]
> Model text is never used as a command or argument, except file paths validated to sit inside
> the worktree.

## Timeline

| Date | What happened |
| --- | --- |
| 2026-11-20 | First commit. The brief, the plan, then v1 milestone by milestone: schemas and config, the bug zoo, hardware detection, the model runtime, git snapshots and worktrees, the edit protocol and applier, the verification cascade, grades and receipts, the solve controller, the terminal UI, the MCP server, the doctor. Then v1.1 the same day: learning from applied fixes, the Docker sealed test box, JavaScript and TypeScript, quiet mode, one-click agent connect. 51 commits that day. |
| 2026-11-20 | A threat-modelling pass: a crafted receipt could pass `--basetemp` to pytest (which deletes the folder it is given), git hooks could run during QEDCode's own commits, an inherited `GIT_DIR` could write to the user's index, option-like file names could slip past checks. All closed, each with a regression test. Relicensed to Apache-2.0. |
| 2026-11-21 | QEDCode Benchy, a separate benchmark built so results cannot be gamed: the same model bare and through QEDCode, judged by hidden tests neither side sees, with Wilson intervals and McNemar tests. Then 143 SWE-bench Verified tasks without Docker. The QEDCode Agent Skill: Antigravity reached grade A on 4 of 4 bug zoo projects from prompts that never named QEDCode. |
| 2026-11-22 | Benchy's agent-mode pilot: on a public SWE-bench task, Gemini named the task from memory and the agent searched the web for the upstream fix. The pilot now blocks web search, and Benchy gained private "fresh" suites of newly planted bugs. |

## The benchmark found real bugs, and each fix came with a test

Benchy was built to measure QEDCode, and the first thing it measured was QEDCode's own mistakes:

1. **The edit parser split code at reST headings.** A SEARCH block containing a sympy-style
   docstring underline (`    ========`) was cut at the underline, so every candidate on
   `sympy__sympy-17630` failed with a syntax error. The exact `=======` divider now wins over a
   loose one. Found by the SWE-bench suite.
2. **`qed check` waited 300 seconds on big repositories.** On sympy one check took 306 s because
   the full suite ran into the project's test timeout before the change was even looked at. The
   full suite now gets a limit per speed, a too-slow suite is remembered, and the same check takes
   126 s (6 s at fast speed). Found by agent mode, where Antigravity plus QEDCode timed out.
3. **A comment-only change was graded B.** A candidate that changed nothing but comments passed
   the tests, because nothing had changed. Changes that alter no code outside tests (compared by
   syntax tree) are now capped at C, and the receipt says why. Found by Benchy's scripted
   self-test. The same rule later caught a `.gitignore`-only change graded B from the Antigravity
   IDE.

Every one of these became a regression test before the fix was committed: the rule "every wrong
A is a bug" applies to the tool itself.

## What the record says to do differently

- **Run a real model on day one.** Everything above was built and tested against a scripted
  stand-in model. That made the engine fast to build and CI free of GPUs, but `PROGRESS.md` still
  says "Real-model results: NOT RUN". Benchy's numbers on real hardware are the next step, and
  they should have come before the second wave of features.
- **Put the benchmark next to the engine from the start.** Benchy found three real bugs in a day.
  Built a day earlier, it would have found them before they shipped.
- **Plan the hardware pass with the features.** Windows, Linux and 4–6 GB laptops are covered by
  CI and mocked detection; the by-hand pass on real machines is still open.
