# Security

QEDCode runs code a model wrote, on your computer, so security reports are welcome and taken
seriously. The safety model is described in [docs/safety.md](docs/safety.md).

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting:
**Security → Report a vulnerability** on this repository. Do not open a public issue.

Include what you found, how to reproduce it (a receipt, a repository or a command), and what an
attacker gains. You will get an answer within a week. Fixed issues are credited in the CHANGELOG
unless you prefer otherwise.

## In scope

- A model reply, a repository's content, a receipt or a receipt link that makes QEDCode run a
  command it should not, write outside its worktrees, or change your files before `qed apply`.
- A way to get a grade without the evidence the grade rules require.
- API keys appearing in files, logs, receipts or prompts.
- The GitHub Action running pull request code with a write token, or posting content it did not
  validate.

## Supported versions

The latest release. Versions up to commit `a7fbe17` were GPL-3.0 and are not maintained.
