# 0006: Relicense from GPL-3.0 to Apache-2.0

**Status:** Accepted (2026-11-20, commit `8824706`)

## Decision

QEDCode moved from the GNU GPL-3.0 to the Apache License 2.0, with a `NOTICE` file and the SPDX
expression `Apache-2.0` in the package metadata. Versions up to and including commit `a7fbe17`
(including 0.1.0) were released under GPL-3.0, and that licence still applies to copies of them.
The Benchy repository is MIT-licensed on purpose.

## Why

QEDCode is meant to be called by other tools: coding agents through MCP, CI systems through the
GitHub Action, companies' own pipelines. A permissive licence with an explicit patent grant lets
all of them adopt it without a legal review of copyleft obligations.

## Consequences

- Anyone may embed QEDCode in closed-source tools.
- Contributions come in under Apache-2.0 terms as well.
