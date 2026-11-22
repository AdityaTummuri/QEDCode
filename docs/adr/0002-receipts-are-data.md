# 0002: Receipts are data, not scripts

**Status:** Accepted (security hardening, 2026-11-20)

## Context

A receipt lists the commands that produced its proof, so `qed verify` can re-run them. The first
version re-ran whatever a receipt listed. A threat-modelling pass found that a crafted receipt
could then start any program, or pass pytest options such as `--basetemp`, which deletes the folder
it is given.

## Decision

`qed verify` treats a receipt as data. It only ever runs the project's own test command (from
`.qed/project.toml`, or the default `{python} -m pytest`), and the test selection may name only
files and test ids inside the project. Anything else is refused with "not safe to run here". The
patch is checked against its SHA-256 before anything runs.

## Consequences

- A receipt someone hands you, or a receipt link, cannot start anything but your own tests.
- A receipt made with a different test command cannot be verified until `test_command` matches.
- The same rule protects receipt links ([receipts](../receipts.md)) and the GitHub Action.
