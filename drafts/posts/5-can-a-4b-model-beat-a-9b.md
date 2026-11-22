# Can a 4B model with checks beat a 9B model alone?

**Draft: every number below comes from the Benchy main run and must be filled from its report.**

Small local models fit on a 6 GB laptop GPU, but they are wrong often enough that one answer
cannot be trusted. QEDCode's bet: several tries, deterministic checks, keep only what can be
proven. Benchy measured whether that bet pays, with hidden tests neither side saw.

| Model | Bare: solved | With QEDCode: solved | Lift (95% CI) | McNemar p |
| --- | --- | --- | --- | --- |
| Qwen3.5 2B | [FILL] | [FILL] | [FILL] | [FILL] |
| Qwen3.5 4B | [FILL] | [FILL] | [FILL] | [FILL] |
| Qwen2.5-Coder 7B | [FILL] | [FILL] | [FILL] | [FILL] |
| Qwen3.5 9B | [FILL] | [FILL] | [FILL] | [FILL] |

**The size jump:** 4B + QEDCode solved [FILL: k/n]; 9B alone solved [FILL: k/n]. [FILL: say
plainly whether it matched, beat or fell short.]

**Grade honesty:** when QEDCode said A, the hidden tests agreed [FILL: x of y] times. [FILL: every
wrong A, what caused it, and the fix.]

**Silent failures:** the bare model handed back [FILL] wrong changes that looked done; QEDCode
handed back [FILL], the rest arrived as C, D or "no fix".

**Where it did not help:** [FILL: task types, e.g. no tests, vague requests].

Hardware: [FILL]. Suites: benchy-core + bug zoo (18 tasks), [FILL] repetitions. Reproduce:
[the notebook](https://github.com/AdityaTummuri/QEDCode/blob/main/notebooks/reproduce.ipynb).
