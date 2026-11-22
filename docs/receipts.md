# Receipts: proof you can hand to someone

Every `qed solve` and `qed check` result comes with a **receipt**: a small JSON file in
`.qed/receipts/` that lists what was changed and every check that ran. It is an open format: any
tool can read it, and `qed verify` re-runs it.

## What a receipt proves

| Field | What it holds |
| --- | --- |
| `grade` | A, B, C or D, written by one function (`verify/grade.py`) and nowhere else |
| `patch`, `patch_sha256` | the exact change (`git diff --binary`) and its checksum |
| `base_commit` | the snapshot the change was made against |
| `checks` | every stage that ran (apply, parse, scope, risk, lint, fail-before/pass-after, related tests, full suite), with its command, exit code, time and test results |
| `fbpa_tests` | the tests that failed before the change and pass after it: the heart of grade A |
| `mutation` | proof strength: how many small breakages of the changed lines the new test catches (when it ran) |
| `unverified` | what is **not** proven, in plain English (for example "Tests ran on this computer without network blocking") |
| `model_info` | backend, model, context size, temperatures and how many model calls were made |
| `commands` | what `qed verify` re-runs, with the interpreter and report path as placeholders |

Receipts never contain API keys, prompts or model replies, only the patch.

## Re-run one

```bash
qed verify .qed/receipts/<task id>.json
```

`qed verify` re-checks the patch's checksum, re-creates the base commit and the patched code in
fresh worktrees, re-runs the recorded tests on both, and prints `VERIFIED` only if every recorded
result still holds. A receipt is **data, not a script**: `qed verify` only ever runs your
project's own test command on test files inside the project, so a receipt someone hands you
cannot start anything else.

## Share one as a link

```bash
qed receipt --link
```

prints a link to the **Receipt Viewer**:

```text
https://adityatummuri.github.io/QEDCode/receipt/#r=H4sIAAAAAAACE-1YX2_bNhD_KoSeGkSyRNlybAM...
```

The whole receipt (gzip-compressed, base64url) rides in the part after `#`, which browsers never
send to a server. Nothing is uploaded or stored anywhere: paste the link in a pull request and a
reviewer clicks straight to the evidence. The viewer decompresses it in the browser, checks it
against the receipt schema and recomputes the patch checksum, so an edited link shows a
checksum mismatch. The same receipt always gives the same link.

Because the diff is inside the link, share links only where that code may go. `qed verify` also
accepts a link instead of a file, inside the repository the receipt came from.

## Read one

```bash
qed receipt                 # the newest receipt in this project, in plain English
qed receipt path/to.json    # a specific one
qed receipt --json          # the raw file
```

You can also drop a receipt file onto the [Receipt Viewer](https://adityatummuri.github.io/QEDCode/receipt/)
page; it is read by the browser and never leaves your computer.

## Receipts in git

`qed apply --commit` commits an applied fix with `QED-Grade` and `QED-Receipt` trailers and stores
the receipt in git notes (`refs/notes/qed`), so the proof travels with the repository:

```bash
git log --notes=qed          # every applied fix with its receipt
```

## The schema

The receipt is defined by the `Receipt` model in
[`src/qedcode/models/schemas.py`](https://github.com/AdityaTummuri/QEDCode/blob/main/src/qedcode/models/schemas.py)
(`schema_version: 1`). A JSON Schema generated from it ships with the Receipt Viewer
(`receipt/receipt.schema.json` on the website), so other tools can validate receipts too. New
fields are only ever added with a default, so old receipts stay valid.
