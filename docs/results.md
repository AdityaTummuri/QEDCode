# Results

QEDCode's claim is simple: a small local model with QEDCode hands you more correct fixes, and
tells you honestly when it has none. These numbers measure that claim with
[QEDCode Benchy](https://github.com/AdityaTummuri/QEDCode-benchy), a benchmark built so the result
cannot be gamed: the model and the tool never see the tests that judge them.

!!! note "Status"
    Real-model runs are in progress on the author's own hardware. Until a run is published here,
    this page lists only what will be measured and how. No number on this site is hand-typed:
    each one links to the report it came from.

## The four headline numbers

| Number | Question it answers | How it is measured |
| --- | --- | --- |
| **Resolution lift** | Does QEDCode solve more tasks than the same model alone? | Share of tasks whose hidden tests pass, bare model vs. QEDCode, per model, with 95% Wilson intervals and a paired McNemar test |
| **Grade honesty** | When QEDCode says A, is it right? | Of results graded A (and B), the share that pass the hidden tests. Every wrong A is treated as a bug |
| **Silent failures** | How often does a tool hand back a change that looks done but is wrong? | A bare model's wrong answers are all silent; QEDCode's should arrive as C, D or "no fix" |
| **The size jump** | Can a small model with QEDCode match a bigger model alone? | For example 4B + QEDCode against 9B on its own, on the same tasks |

## The runs

| Run | Suite | Models | Why |
| --- | --- | --- | --- |
| Smoke | `benchy-core` (8 tasks) | Qwen3.5 4B | proves the pipeline end to end |
| Main | `benchy-core` + bug zoo (18 tasks) | Qwen3.5 2B, 4B, 9B and Qwen2.5-Coder 7B | the model-size curve, bare vs. QEDCode |
| Real repositories | `swebench-verified-py` (a 10-task pilot) | the largest model that fits | real-world credibility |
| Unseen bugs | `benchy-fresh` (34 tasks, private) | Qwen3.5 4B and 9B | bugs planted after the models were trained, so none can be remembered; totals only |
| Hardware pass | 2 bug zoo tasks | the recommended model per machine | fills the [Tested on](install.md#tested-on) table |

The losses stay in. Task types where QEDCode does not help (no tests, vague requests) are named
in each report.

## Reproduce it

Every run can be repeated on your own machine:

```bash
qed benchy install
qed benchy run --suite benchy-core --reps 3          # uses the model `qed` is set up with
qed benchy run --model ollama:qwen3.5:4b --reps 3    # or any Ollama model
qed benchy report
```

No GPU? The [reproduce notebook](https://github.com/AdityaTummuri/QEDCode/blob/main/notebooks/reproduce.ipynb)
runs the smoke run on a free notebook GPU.
