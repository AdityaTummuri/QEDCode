# 0007: No backend

**Status:** Accepted (roadmap, 2026-11-22)

## Context

A hosted app that runs solves, accounts, a database or telemetry would each look good for a week
and then cost money, attract abuse and need uptime forever.

## Decision

Everything runs on a machine the user already has: their computer (the CLI, the model), their
browser (the website, the Receipt Viewer, Fool the Grader), or their CI (the GitHub Action). When a
feature seems to need a server, it is redesigned: a receipt rides inside the URL fragment instead
of being uploaded, benchmark results arrive as pull requests, and the website is static on GitHub
Pages.

## Consequences

- No bills, no accounts, no data about users anywhere.
- The project keeps working when nobody maintains a server.
- Features that truly need shared state (for example a live leaderboard with anonymous uploads)
  are out of scope.
