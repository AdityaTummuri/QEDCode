# Safety model

QEDCode runs code a model wrote, on your computer. These are the rules that keep that safe, and
the limits it states openly.

- Work happens in git worktrees under `.qed/worktrees/`; your working tree is written only by
  `qed apply` / `qed undo`, which refuse if a touched file changed since the snapshot.
- The model never runs commands. Only fixed commands built by QEDCode run: git, your project's
  Python with pytest, ruff, Node with your test runner, and the llama.cpp server. File names the
  model writes may not contain quotes or control characters or start with `-`, so they can never
  be read as options or hide from the checks.
- QEDCode's own git commands (snapshots, candidate commits, worktrees) never run git hooks, and
  ignore `GIT_DIR`, `GIT_INDEX_FILE` and similar variables, so `qed check` is safe to call from a
  pre-commit hook and never touches your index.
- Receipts are data: `qed verify` refuses a receipt that asks to run anything other than your
  project's test command (see `test_command` in `.qed/project.toml`) on files inside the project.
- Existing tests and test configuration are protected: a candidate that edits, deletes, skips or
  weakens them is rejected. A candidate that adds risky code (`subprocess`, network, `eval`, ...)
  needs your approval and can then reach grade B at most.
- `qed check` grades your own changes, so it reports instead of rejecting. Editing an existing
  test caps the grade at B; weakening one (adding a skip, removing asserts, or an assert that can
  never fail such as `assert True or ...`) caps it at C and the summary says `tests weakened`.
  Adding new test functions to an existing Python test file is not a protected change: those
  tests can prove the fix and reach grade A.
- Every download is announced with its size and asked first; API keys live only in the system
  keychain or environment variables; project commands run with secret-looking variables removed.
- By default tests run on your computer, without network blocking or a container, and every
  receipt says so under "not proven". `qed sandbox setup` (needs Docker) builds a **sealed test
  box**: all tests then run in a container with the network off, and the receipt says so.
