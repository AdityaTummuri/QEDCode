# GitHub Action and pre-commit

**Catch AI pull requests that cheat their tests.** The QEDCode Check action runs `qed check` on
every pull request: no model, no GPU, on your own CI minutes. It posts one comment with the grade,
the reason and a link to the proof, and can fail the check below a grade you choose.

```text
∎ QEDCode: Grade C
Grade C · 41/41 tests pass · lint clean · protected files changed · tests weakened in tests/test_cart.py

To raise the grade: Undo the change that weakens tests in tests/test_cart.py (a skip, removed
asserts or an assert that can never fail); passing tests prove nothing while it is there.

Open the proof in the Receipt Viewer · receipt and log (artifact qedcode-check) · re-run it locally with qed verify <receipt>
```

## Add it

`.github/workflows/qedcode.yml`:

```yaml
name: QEDCode
on:
  pull_request:

permissions:
  contents: read
  pull-requests: write   # for the comment

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0             # QEDCode compares with the pull request's base commit
      # Set the project up the way your tests need it, so `pytest` works:
      - uses: astral-sh/setup-uv@v5
      - run: uv sync                 # or: pip install -e ".[test]"
      - uses: AdityaTummuri/QEDCode@v0.2.0
        with:
          fail-below: B              # optional: make it a required check
```

QEDCode runs your tests twice, once on the base commit and once on the pull request, in throw-away
git worktrees. It uses the project's `.venv/` when there is one, otherwise `python3` on the PATH.

## Inputs

| Input | Default | What it does |
| --- | --- | --- |
| `fail-below` | `none` | fail the job when the grade is below `A`, `B`, `C` or `D` (a rejected change or a check that could not run counts as lowest) |
| `speed` | `normal` | `fast` skips the full suite (best grade B), `sure` allows more time |
| `base` | the PR's base commit | compare with another commit |
| `version` | `qedcode` | what to install, as a `uvx --from` spec (for example `qedcode==0.2.0`, or a git URL) |
| `working-directory` | `.` | the project folder inside the repository |
| `comment` | `true` | post or update one sticky comment |
| `attest` | `false` | sign the receipt with a GitHub artifact attestation (see below) |
| `github-token` | `github.token` | the token used for the comment |

Outputs: `grade`, `status`, `summary`, `next-step` and `receipt-link`. The receipt, the comment
and the raw result are kept as the run's `qedcode-check` artifact, and the comment is also written
to the job summary.

## Pull requests from forks

A pull request from a fork gets a read-only token, so the check job cannot comment. Never switch
to `pull_request_target` to get around it: that would run untrusted code with a write token.
Instead keep the check on `pull_request` and add a second workflow that only posts the comment the
check saved. It never checks out or runs the pull request's code:

```yaml
name: QEDCode comment
on:
  workflow_run:
    workflows: [QEDCode]
    types: [completed]

permissions:
  pull-requests: write
  actions: read

jobs:
  comment:
    if: github.event.workflow_run.event == 'pull_request'
    runs-on: ubuntu-latest
    steps:
      - uses: AdityaTummuri/QEDCode@v0.2.0
        with:
          mode: comment
```

Before posting, the comment step checks that the pull request's head commit is the one the check
ran on, and that the saved file is a QEDCode comment.

## Signed receipts

With `attest: true` (and `permissions: id-token: write, attestations: write`), the receipt is
signed with a [GitHub artifact attestation](https://docs.github.com/actions/security-guides/using-artifact-attestations-to-establish-provenance-for-builds)
through Sigstore's public-good instance. Public repositories get this for free. Anyone can then
check that a receipt really came from that CI run, not only that it is well formed:

```bash
gh attestation verify receipt.json --repo <owner>/<repo>
```

## pre-commit

Grade every commit before it is made. In `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/AdityaTummuri/QEDCode
    rev: v0.2.0
    hooks:
      - id: qedcode-check
```

The hook runs `qed check --speed fast --fail-below B`: a commit that weakens a test (grade C) is
stopped, and the message says what to undo. QEDCode's own git commands never run git hooks and
ignore `GIT_DIR` and `GIT_INDEX_FILE`, so it is safe inside a hook and never touches your index.

Without pre-commit, the same line works in any CI system or a plain `.git/hooks/pre-commit` script:

```bash
uvx qedcode check --base origin/main --fail-below B
```
