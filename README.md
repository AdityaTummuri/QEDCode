# QEDCode

QEDCode is a local-first coding engine that makes small language models (4–6 GB of VRAM) reliable.
Instead of trusting a model's first answer, it asks for several candidate changes, tests each one in a
safe git worktree, and returns only the best verified change with an evidence grade (A–D) and a proof
receipt that anyone can re-run with `qed verify`. Your files are never touched until you apply.

## Run the tests

```bash
uv sync
uv run pytest -m "not gpu"
uv run ruff check .
```

The tests need no GPU, no network and no real model: a scripted `FakeModel` stands in for the AI.
