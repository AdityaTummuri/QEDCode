# Threat-modelling an AI coding tool

QEDCode runs code that a model wrote, on your computer, and it reads files other people send you.
After v1 worked, I attacked it. Four holes, all closed, each with a regression test.

1. **A receipt that runs anything.** `qed verify` re-ran the commands a receipt listed. A crafted
   receipt could start any program, or pass pytest `--basetemp=<folder>`, which deletes that
   folder. Now a receipt is data: only the project's own test command runs, and the test
   selection may name only files and test ids inside the project.
2. **Git hooks.** QEDCode makes its own commits (snapshots, candidates). Those ran the project's
   git hooks, and a candidate could add a hook file. Now QEDCode's git commands point
   `core.hooksPath` at nothing.
3. **An inherited `GIT_DIR`.** Run from a pre-commit hook, `qed check` inherited `GIT_DIR` and
   `GIT_INDEX_FILE` and could write to your index. Now QEDCode ignores git's location variables.
4. **Option-like file names.** A model could name a file `-rf` or put quotes and control characters
   in a path, so it reads as an option or hides from a check. Paths with those are refused, and
   the diff reader decodes git's quoted names so no file slips past.

Underneath sits the rule that made the rest possible: **the model never runs commands.** It
returns text edits; only fixed commands built by QEDCode run. Prompt injection hidden in a README
or an issue has nowhere to land.

What is still open is said in every receipt: by default tests run on your machine without network
blocking. `qed sandbox setup` runs them in Docker with the network off.
