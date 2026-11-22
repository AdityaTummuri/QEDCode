# 0003: Docker is optional

**Status:** Accepted (v1.1, `AGENTS.md` section 15.2)

## Context

Running model-written code and tests on the host, without network blocking, is v1's biggest
safety limit. A container with the network off removes it. But Docker is a large install, needs
admin rights on some systems, is often unavailable on the 4–6 GB laptops QEDCode targets, and
would turn every first run into a multi-gigabyte setup.

## Decision

The sealed test box is opt-in: `qed sandbox setup` builds an image (after asking, because it
downloads), and from then on every test run uses `docker run --network none --cap-drop ALL
--read-only`. Without it, tests run on the host and every receipt says so under "not proven".
QEDCode never installs Docker itself (`AGENTS.md` rule 7).

## Consequences

- First runs stay small and work everywhere git and Python work.
- The safety gap is never hidden: the receipt states it every time.
- Python projects only for now; the project's dependencies are frozen into the image at setup.
