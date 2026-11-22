# 0004: Benchy has no submission server

**Status:** Accepted (Benchy plan revision, 2026-11-21)

## Context

Benchy's first plan had a server-side submission service and a GitHub App that would open
anonymous pull requests with community benchmark results.

## Decision

Not built. `benchy share` shows exactly what a result would contain, `benchy share --export` saves
it as a file, and contributors open the pull request themselves (the docs say plainly that it
shows their GitHub account). CI validates the record; merged means accepted.

## Why

A service needs hosting, a privileged credential, abuse controls and ongoing operation, and it is a
large attack surface. Community results cannot be verified anyway (a passing schema check does not
prove a run happened), so anonymity buys little. The record format works unchanged behind a service
if one is ever needed.

## Consequences

- Nothing leaves a user's machine without an explicit, visible action.
- Zero hosting cost and nothing to keep running.
- Fewer submissions than a one-click button would get.
