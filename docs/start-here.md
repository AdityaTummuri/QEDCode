# Using Claude Code, Cursor or Copilot? Start here

Your agent already writes the code. QEDCode checks it. You don't need a GPU, a local model or any
setup for this part: `qed check` runs no model at all.

## The problem it catches

Agents say "fixed" when it isn't, and sometimes they get the tests green by weakening them: a skip
added, an assert deleted, `assert True or ...`, a test file edited. `qed check` grades the change
from evidence, not from what the agent says.

## Two commands

Let your agent make its change, then in the repository:

```bash
uvx qedcode check
```

`uvx` comes with [uv](https://docs.astral.sh/uv/) and runs QEDCode without installing it. It
compares your uncommitted work with `HEAD` (use `--base main` for a whole branch), runs the tests in
a throw-away copy, and prints a grade with the reasons:

```text
$ uvx qedcode check
Taking a snapshot of your current files...
Running your tests on HEAD to see what already passes...
Checking your changes...
u1 · scope: 2 thing(s) to look at in your change (not blocking).
u1 · fbpa: skipped (no new test)
u1 · full: 4/4 tests pass.

Caught: the tests were changed to look green
  - tests/test_scores.py: test_top_scores is now skipped or expected to fail
Grade C
Grade C · 4/4 tests pass · lint clean · protected files changed · tests weakened in tests/test_scores.py
Receipt: .qed/receipts/t-20261123-010813-ddf3.json
```

That change made `top_scores` "pass" by skipping the test that caught it (real output on a bug zoo
copy, trimmed to ten lines). A real fix that comes with a new test that fails on the old code gets
grade A instead:

```text
u1 · fbpa: 1 test(s) failed before the change and pass now.
u1 · full: 5/5 tests pass.

Grade A
Grade A · repro test failed before, passes now · 5/5 tests pass · lint clean · no protected files changed
```

| Grade | Meaning |
| --- | --- |
| **A** Proven | a new test failed before the change and passes after it, the full suite shows no regressions, no test was touched |
| **B** Tested | the tests pass, but nothing proved the exact problem was fixed |
| **C** Checked | it parses and lints, but no test covers it, or a test was weakened |
| **D** Not checked | nothing could run, and it says why |

## Make it automatic

- **Ask your agent to do it.** Connect QEDCode once (`qed connect`, or
  `claude mcp add qedcode -- uvx qedcode mcp`) and tell it: "check your change with qedcode before
  you say done". The [Agent Skill](https://github.com/AdityaTummuri/QEDCode-AgentSkill) teaches
  agents to do this without being asked.
- **In every pull request.** Add the [QEDCode Check action](github-action.md): it posts the grade
  as a comment and can fail the check below a grade you choose.
- **Before every commit.** Use the [pre-commit hook](github-action.md#pre-commit).

## Share the proof

Every check writes a receipt. `qed verify <receipt>` re-runs it on any machine, and
`qed receipt --link` makes a link that opens it in the [Receipt Viewer](https://adityatummuri.github.io/QEDCode/receipt/)
(the receipt rides inside the link; nothing is uploaded).

## Want QEDCode to write the fix too?

That's `qed solve`, with a small local model or a cloud model: see [Install](install.md).
