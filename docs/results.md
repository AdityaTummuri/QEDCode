# Results

QEDCode's claim is simple: a small local model with QEDCode hands you more correct fixes, and
tells you honestly when it has none. These numbers measure that claim with
[QEDCode Benchy](https://github.com/AdityaTummuri/QEDCode-benchy), a benchmark built so the result
cannot be gamed: the model and the tool never see the tests that judge them.

!!! note "Status"
    The first real-model runs are below: a 6 GB laptop, two small easy suites. The larger runs
    (SWE-bench Verified, the private fresh suite, the 2B and 9B models) are still to come. Every
    number links to the report it came from.

## First look: a 6 GB laptop (2026-11-23)

RTX 4050 Laptop GPU (6 GB), models served by Ollama, normal speed, 3 repetitions per task.
[The four reports](https://github.com/AdityaTummuri/QEDCode-benchy/tree/main/results/pilots/2026-11-23-ollama-rtx4050-laptop).

| Model | Suite | Bare model solved | With QEDCode solved | McNemar p |
| --- | --- | --- | --- | --- |
| qwen3.5:4b | benchy-core (8 tasks) | 19/24 (79%) | 22/24 (92%) | 0.375 |
| qwen3.5:4b | bug zoo (10 tasks) | 28/30 (93%) | 26/30 (87%) | 0.625 |
| qwen2.5-coder:7b | benchy-core | 24/24 (100%) | 23/24 (96%) | 1.0 |
| qwen2.5-coder:7b | bug zoo | 26/30 (87%) | 26/30 (87%) | 1.0 |

- **Tasks solved: no significant difference.** These suites are easy: the bare models already
  solve 79-100% of them, so there is little room to lift.
- **Grade honesty: 86 of 89 grade A results were right.** The 3 wrong A's are one
  under-specified task (`008_slugify_feature`: "drop punctuation", and the model also dropped
  dashes, which the hidden test keeps). The task text was not changed afterwards.
- **Silent failures: 11 of 108 for the bare models, 4 of 108 with QEDCode.** A bare model's wrong answer always
  looks done; QEDCode's other misses came back as "no fix" instead of a wrong change.
- **After the fixes those runs led to** (a try may no longer edit the test that shows the
  problem; a broken repro test is rewritten), the same suites gave: grade A right **62 of 65**
  times (all 3 misses on the under-specified task 008), but fewer tasks solved with qwen3.5:4b on
  the bug zoo (21/30 through QEDCode vs 28/30 bare). The 4B model's own tests often expect the
  wrong values, and QEDCode now says "no fix" instead of passing a test the fix rewrote. Better
  tests from small models are the next piece of work.
- **SWE-bench Verified pilot** (10 real issues, qwen3.5:4b, one attempt): 0/10 bare, 2/10 through
  QEDCode, both right. Every bare reply was an unusable diff, so part of that lift is QEDCode's
  edit format, not only its checks.
- **The runs found a real bug in QEDCode** (a stale fail-before list after a repair edited the
  repro test); `qed verify` caught it and it is fixed. See the
  [replay](https://adityatummuri.github.io/QEDCode/replays/).

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
