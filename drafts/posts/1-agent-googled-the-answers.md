# My benchmark caught the AI agent googling the answers

I built [Benchy](https://github.com/AdityaTummuri/QEDCode-benchy) to measure one thing honestly:
does my tool, QEDCode, help a coding agent fix real bugs? Same agent, same tasks, twice: once on
its own, once with QEDCode, judged by hidden tests neither side sees.

The first real run used SWE-bench Verified, the standard benchmark of real GitHub issues. On the
first task the agent alone resolved the issue in 738 seconds. A pass. Then I read the event log.

**Before writing its fix, the agent ran eight web searches.** SWE-bench issues are public, and so
are the upstream commits that fixed them. The model also named the task from memory. A pass on a
task the agent can look up measures lookup, not fixing.

## What changed

- **No internet on either side.** Both workspaces get a `PreToolUse` hook that refuses the
  web-search and browser tools (permission rules do not cover `search_web`), and both runs deny
  URL reads and network commands (`gh`, `curl`, `wget`, `git fetch`, `pip`, ...). The report now
  counts refused web calls per attempt.
- **Fresh suites.** Public tasks leak into training data, so Benchy can now plant new bugs in real,
  well-tested projects (boltons, pyparsing, tomlkit, more-itertools, networkx): small edits that
  make 1-10 existing tests fail, reviewed by hand. The removed tests become the hidden tests. The
  tool is public; the 34 tasks are private, so no model can have seen their fixes.

## The lesson

If your benchmark's answers are on the internet, your agent will find them, and your numbers will
look great. Read the logs, not only the scores.

Benchy also caught two bugs in QEDCode itself on the same day; that story is in
[the case study](https://adityatummuri.github.io/QEDCode/docs/case-study/).
