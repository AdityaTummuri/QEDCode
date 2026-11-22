# Receipts for AI code

"The agent says it's fixed" is a claim. A receipt is evidence you can re-check.

Every QEDCode result writes one: a small JSON file with the patch and its SHA-256, the commit it
applies to, every check that ran (command, exit code, time, test results), the tests that failed
before the change and pass after it, a mutation score, and, in plain English, what is **not**
proven ("tests ran without network blocking"). It never contains prompts, model replies or keys.

## Re-running one

`qed verify receipt.json` re-checks the checksum, re-creates the old and the new code in fresh git
worktrees, re-runs the recorded tests on both, and prints VERIFIED only if every result still
holds. A receipt is **data, not a script**: verify only ever runs your project's own test command
on test files inside your project, so a receipt someone hands you cannot run anything else. (An
early version did run whatever a receipt listed; the threat-modelling post explains why that was
a hole.)

## Sharing one without a server

`qed receipt --link` packs the whole receipt into a link: gzip, base64url, after the `#`. Browsers
never send the fragment to a server, so nothing is uploaded or stored anywhere. Paste it into a
pull request and a reviewer opens the
[Receipt Viewer](https://adityatummuri.github.io/QEDCode/receipt/?sample), which checks it against
the schema and recomputes the checksum in the browser.

## An open format

The schema is generated from one Pydantic model and published with the viewer. If you build a
coding tool, you can write the same receipts; anything that reads them, including `qed verify`
and the viewer, works with yours.
