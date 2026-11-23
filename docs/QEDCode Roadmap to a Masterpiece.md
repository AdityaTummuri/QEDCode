# QEDCode: Roadmap to a Masterpiece

Nov 22, 2026 · @Susamanth Nellore

## Verdict: a strong engine with invisible proof

QEDCode is already engineered better than most portfolio projects. What it lacks is proof people can see: real-model numbers, a demo, a one-line install and a page to land on. A tool named after the end of a proof has not yet shown its own result, and that is the first thing to fix.

**Already excellent**

- **A real product, not a prototype.** About 14k lines of source, 1,248 passing tests in the last full run, CI on Linux, macOS and Windows, and a CI check that the engine never imports a UI.
- **Serious safety design.** Worktree isolation, a model that never runs commands, receipts treated as data (the `--basetemp` attack is blocked), git hooks and `GIT_DIR` neutralised, keys only in the keychain.
- **Honest by construction.** One function writes every grade, each receipt lists what is not proven, and the README admits there is no benchmark yet.
- **A real ecosystem.** The engine, an Agent Skill that took Antigravity to 4 of 4 grade A results from prompts that never named QEDCode, and Benchy, a fair benchmark with Wilson intervals, McNemar tests and 143 SWE-bench Verified tasks without Docker.
- **A self-correcting loop.** Benchy already found real engine bugs: the reST-underline parser split, the 300 s `qed check` wait, and comment-only changes graded B.
- **A brand.** The name, the `<∎>` mark and "Small local models. Proven fixes."

**What holds it back**

| # | Gap | What I found | Fixed in |
| --- | --- | --- | --- |
| 1 | No real-model numbers | PROGRESS.md says "Real-model results: NOT RUN"; Benchy's leaderboard has 0 submissions; the agent pilot finished 1 task pair | Phase 1 |
| 2 | No demo | The README hero is still `<!-- DEMO: replace this line ... -->` | Phases 0 and 1 |
| 3 | Hard to install | PyPI answers 404 for `qedcode`; the QEDCode repo has no tag or release; install needs a git URL | Phase 0 |
| 4 | Invisible on GitHub | QEDCode repo has no description, topics or website and 0 stars; GitHub Pages is off on all three repos | Do today |
| 5 | Release on the wrong repo | "QEDCode Agent Skill v0.3.1" is published on the Benchy repo; the Skill repo has no releases | Do today |
| 6 | Unsaved work | Benchy has 6 modified files plus untracked `assets/`, `docs/fresh-suites.md` and `tools/plant_bugs.py` | Do today |
| 7 | Stale numbers | The README badge says 1,118 tests; the last full gate says 1,248 | Phase 0 |
| 8 | README too long for a first visit | 31 KB: the 30-second pitch is buried under install guides for three systems | Phase 0 |
| 9 | Unchecked on real hardware | Step 8.3 (Windows RTX 5060 Ti, 4-6 GB laptops), real Docker and real VS Code are still open | Phase 1 |
| 10 | A Word file in `docs/` | "What It Is.docx" does not render on GitHub | Phase 0 |

The build brief and "What It Is" carry the byline @Susamanth Nellore. If that is a collaborator, credit them in the README and the case study; clear attribution reads well to reviewers.

## Do today: 30 minutes of quick wins

Eight small fixes make the project look finished before any new code is written. None needs a server or money.

- [ ] Commit and push Benchy's pending work. First check that `tools/plant_bugs.py` and `docs/fresh-suites.md` hold no answers for the private fresh suite.
- [ ] Move the Skill release: publish v0.3.1 with its zip on QEDCode-AgentSkill, then delete it from the Benchy repo.
- [ ] Give QEDCode a description: "Make small local LLMs' code fixes provable: several tries, deterministic checks, one graded fix with a re-runnable receipt."
- [ ] Add topics to all three repos: `local-llm`, `coding-agent`, `mcp-server`, `verification`, `testing`, `pytest`, `llama-cpp`, `ollama`, `python`, `typescript`.
- [ ] Upload a 1280 x 640 social preview image (the `<∎>` mark and the tagline on dark), so every shared link shows a card.
- [ ] Pin the three repos on your GitHub profile and turn on Discussions for QEDCode.
- [ ] Turn on private vulnerability reporting and add a short `SECURITY.md`.
- [ ] Add a second console script, `qedcode = "qedcode.cli:app"`, so `uvx qedcode` works once it is on PyPI. Benchy already does this with `qedcode-benchy`.

## Ground rules: you never pay for the compute

Every item in this plan runs on a machine you do not pay for: your own GPU, the visitor's browser, or the adopter's CI.

| Where it runs | Used for | Cost to you |
| --- | --- | --- |
| Your computers (RTX 5060 Ti PC, Mac, laptops) | Benchmarks, recordings, overnight runs | Electricity |
| The visitor's browser | Website, Receipt Viewer, Fool the Grader, replays | Nothing (GitHub Pages) |
| The adopter's CI | The QEDCode GitHub Action on their pull requests | Nothing (their Actions minutes) |
| GitHub's free tier for public repos | Your CI, Pages, Releases, Discussions | Nothing |
| PyPI, Hugging Face Hub, YouTube | The package, model weights, the demo video | Nothing |

Five rules keep it that way:

1. **No backend, accounts, database or telemetry.** If a feature needs a server, redesign it: the receipt rides inside the URL, and results arrive as pull requests.
2. **Pin what can break.** The llama.cpp tag and model revisions are already pinned; pin action versions too. It must still work after six months without you.
3. **Generated, never hand-kept.** Screenshots, GIFs, stats, leaderboards and release notes are rebuilt by CI, so they never go stale.
4. **One engine.** Every feature reuses try, test, prove, or adds a front door to it.
5. **Every item ends with "Done when".** Same style as your build plan, so each one can go to an AI agent as written.

## North star: the proof layer for AI-written code

Position QEDCode as **the proof layer for AI-written code**, with small local models as its free engine. That widens the audience without changing the engine.

Today the pitch speaks to people running 4-6 GB local models. But `qed check` and `qed verify` need no model at all. They solve a problem every Claude Code, Cursor and Copilot user has: an agent says "fixed" when it isn't, or turns tests green by weakening them.

| Audience | Their problem | What they use | GPU needed? |
| --- | --- | --- | --- |
| People using big cloud agents (the larger group) | "Done" that isn't; tests quietly weakened | `qed check`, the GitHub Action, receipts, the Agent Skill | No |
| Local-model users | Small models are wrong too often to trust | `qed solve`: several tries, one proven fix | Yes, or a cloud key |

One-line pitch: **"QEDCode makes AI show its work: every code change gets a grade, a reason and a receipt anyone can re-run."** Keep "Small local models. Proven fixes." as the tagline for the local door.

Each phase below serves one of three jobs: prove the claim, make it trivial to try, or put it where developers already work.

## Phase 0: ship what exists (week 1)

Release v0.2.0 to PyPI and rebuild the README's first screen. The CHANGELOG's Unreleased section already holds a release's worth of fixes.

1. **PyPI release.** Add the pending trusted publisher on PyPI once (project `qedcode`, workflow `release.yml`, environment `pypi`); your workflow does the rest. Move Unreleased to `[0.2.0]`, bump the version, push tag `v0.2.0`. Add a step that creates the GitHub Release from that CHANGELOG section. Publish `qedcode-benchy` the same way, so `qed benchy install` needs no git URL.
   - Done when: `uvx qedcode --version` prints 0.2.0 on a clean machine and the release page shows the notes.
2. **A README first screen that sells in 30 seconds.** Order: logo and tagline; a 20-second GIF; the three headline numbers from Phase 1; three commands (try it, check a change, full setup); the check-ladder diagram; "works with" names; links into the docs site. Move per-OS install, commands, safety and troubleshooting to the docs site (Phase 3). Aim for under 8 KB.
   - Done when: a stranger can say what it does, why to trust it and how to try it without scrolling.
3. **No hand-kept numbers.** Swap the "1,118 tests" badge for the live CI status badge. Let the release workflow write a stats block (tests, lines, platforms) into the README.
   - Done when: no number in the README can go stale between releases.
4. **Screenshots that rebuild themselves.** Textual can save an SVG screenshot of the running app. Have a pilot test save the result card, diff view and receipt view into `docs/assets/` on each release, using the scripted model.
   - Done when: every README image is produced by CI, not by hand.
5. **Markdown, not Word.** Convert "What It Is.docx" to `docs/what-it-is.md`; it becomes the website's overview page.

## Phase 1: prove it with numbers (weeks 1-2)

Run Benchy on your own machines with local models and publish four numbers. It costs nothing, has no quota, and everything else in this plan leans on it.

Use local models for the main table, not agent mode. The Antigravity pilot used about 190-230k tokens per attempt and hit the weekly quota after 5-6 attempts. Your RTX 5060 Ti can run all night for free.

| Run | Suite | Models | Reps | Why |
| --- | --- | --- | --- | --- |
| 1. Smoke | `benchy-core` (8 tasks) | Qwen3.5-4B | 3 | Proves the pipeline in one evening |
| 2. Main | `benchy-core` + bug zoo (18 tasks) | Qwen3.5 2B, 4B, 9B and Qwen2.5-Coder 7B | 3, then 5 | The model-size curve, bare vs QEDCode |
| 3. Real repos | `swebench-verified-py`, your 10-task pilot list | The largest model that fits | 1-3 | Real-world credibility |
| 4. Unseen bugs | `benchy-fresh` (34 tasks, private) | Qwen3.5 4B and 9B | 3 | Answers no model can have memorised; publish totals only |
| 5. Hardware pass | 2 bug-zoo tasks | The recommended model per machine | 1 | Closes step 8.3 and fills a "Tested on" table |

**The four headline numbers**

1. **Resolution lift.** Share of tasks whose hidden tests pass, bare vs QEDCode, per model, with 95% Wilson intervals and the paired McNemar p-value. Benchy already computes all three.
2. **Grade honesty.** Of results graded A, the share that pass the hidden tests (same for B). "When QEDCode says A, it was right X of Y times" is the number that makes the name true, and the one a sceptic asks for first. Every wrong A is a bug to fix, by your own rule.
3. **Silent failures.** How often each side hands back a change that looks done but fails the hidden tests. A bare model's wrong answers are all silent; QEDCode's should arrive as C, D or "no fix".
4. **The size jump.** Does a small model with QEDCode match a bigger model alone, for example 4B + QEDCode against 9B bare? If yes, that is the headline. If no, say so plainly.

**Publishing**

- A short Results section near the top of the README: one chart, the four numbers, a link to each full report.
- Export your runs with `benchy share --export` as the first entries in `results/submissions/`, so the leaderboard and dashboard fill themselves.
- Keep the losses in. Name the task types where QEDCode does not help (no tests, vague tasks); credibility comes from the misses you show.
- Record the hero GIF during run 2: real model, real hardware, sped up, labelled with the model and the speed-up.

Done when: the README shows four measured numbers with intervals, each linked to its report; PROGRESS.md's "Real-model results" lists what ran; every machine you own is in "Tested on".

## Phase 2: anyone can try it in 60 seconds, no GPU

Add `qed try`, a guided demo on a bundled bug-zoo project, so a stranger sees a verified grade A before deciding to download a 3-6 GB model.

1. **`qed try`.** Copies a bug-zoo project into a temporary git repo and runs the whole loop. With no model set up it uses that project's scripted model and says so on screen ("scripted: shows the engine, not model quality"); with a model it is a real run. It then shows the diff, the grade, `qed verify` returning VERIFIED, and a scripted candidate that weakens a test and gets rejected. It ends with three next steps: `qed check` on your repo, `qed setup`, `qed connect`. Ship two or three bug-zoo projects inside the wheel, the way Benchy force-includes its suites.
   - Done when: `uvx qedcode try` on a laptop with no GPU reaches VERIFIED grade A and states what was scripted.
2. **A front door for cloud-agent users.** A docs page, "Using Claude Code or Cursor? Start here": let your agent write the change, then run `uvx qedcode check`. No model, no GPU, no setup.
   - Done when: `qed check` is the README's second command, with a ten-line example of real output.
3. **Open in Codespaces.** A `.devcontainer/devcontainer.json` with uv, QEDCode and a bug-zoo copy, plus a button in the README. The visitor's free Codespaces allowance pays; you keep one JSON file.
   - Done when: the button opens a terminal where `qed try` works.
4. **A reproduce-it notebook.** It installs QEDCode, downloads the 4B model and runs `benchy run` on `benchy-core`, on free notebook GPUs such as Kaggle or Colab when they are available. Anyone can check your numbers without owning a GPU.
   - Done when: the notebook reproduces Phase 1's run 1 end to end.

## Phase 3: the website, all static (weeks 2-3)

Build one static site on GitHub Pages with six parts. Two of them, the Receipt Viewer and Fool the Grader, are what people will remember and share.

Host it free at `adityatummuri.github.io/QEDCode`, rebuilt by an Action on each release. Use a docs generator AI agents handle well (MkDocs Material or Astro Starlight) and plain HTML and JavaScript for the interactive pages. A custom domain is optional and is the only thing in this plan that costs money.

1. **Landing page.** The 30-second pitch: a terminal replay, the four numbers, the check ladder animated as a candidate falls through it, the install line and the "works with" names.
2. **Receipt Viewer: proof you can link to.** Drop a receipt JSON on the page and it renders a certificate: grade seal, the 8-stage ladder with ticks, the diff, tests failing before and passing after, model and temperature, the "not proven" list, the patch checksum. A new `qed receipt --link` packs the receipt into the URL fragment (after `#`), which browsers never send to a server. The link carries the whole proof and nothing is stored anywhere: paste it in a PR and reviewers click straight to the evidence. The CLI warns that the link contains the diff.
   - Done when: a receipt from `qed solve` opens from a link with no network call beyond loading the page, and the page validates the schema and recomputes the checksum in the browser.
3. **Fool the Grader: the creative centrepiece.** An in-browser challenge: a tiny buggy project, its tests and an editor. Goal: grade A. Visitors try the shortcuts an agent might take and watch the grade drop with QEDCode's real reasons: skip the failing test, delete an assert, write `assert True or ...`, edit the test file. Hard-coding the expected value passes the visible tests but fails a hidden one, which teaches why Benchy uses hidden tests. A proper fix earns the A and a shareable score card. Pyodide runs pytest in the browser; reuse the pure-Python guard and grade rules, or port the few that will not load. Levels are bug-zoo projects. No model and no server.
   - Done when: three levels play after the first load, and each cheat shows the same wording the CLI prints.
4. **Benchmark dashboard.** Deploy Benchy's existing `dashboard/index.html`, fed by `leaderboard.json` and the `update-results` workflow you already have. Add the grade-honesty chart and a "best model for a 6 GB card" table.
5. **Replay gallery.** Real sessions from the Phase 1 runs, replayed in the browser as terminal recordings (asciinema files with its static player, or the engine's event log through a small player): "a 4B model fixes a real sympy bug, at 20x speed".
   - Done when: three real-model replays play on the landing page.
6. **Docs and the story.** Per-OS install, commands, agents, the safety model, troubleshooting, FAQ and architecture, plus the case study and decision records from Phase 6.

## Phase 4: put it where developers already work (week 3)

Ship a GitHub Action that grades pull requests with `qed check`. It needs no model, runs on the adopter's CI, and answers a question every team using AI agents now has.

1. **QEDCode Check, a GitHub Action.** An `action.yml` (its own small repo, or the QEDCode root) that checks out full history and runs `uvx qedcode check --base <PR base> --json`. It posts one sticky comment, such as "∎ Grade A · a new test failed on main and passes here · 142/142 tests · no tests weakened", with a Receipt Viewer link and the receipt as an artifact. Below A, the comment names the one step that would earn it ("add a test that fails on main"). Inputs: `fail-below: B` to make it a required check, and `speed`. Pitch: **"Catch AI pull requests that cheat their tests."** For PRs from forks, check in `pull_request` and comment from a separate `workflow_run`; never run PR code under `pull_request_target`. Dogfood it on QEDCode first.
   - Done when: on a sample repo, a PR that skips a failing test gets "Grade C · tests weakened", and a real fix with a new test gets A.
2. **Signed receipts (stretch, very on-brand).** In the Action, sign the receipt with GitHub artifact attestations (`actions/attest`, custom predicate). Public repos get this free, signed through Sigstore's public-good instance with a public transparency log ([GitHub docs](https://docs.github.com/actions/security-guides/using-artifact-attestations-to-establish-provenance-for-builds)). Anyone can then check the proof came from that CI run, which closes the gap Benchy's docs admit: a passing schema check does not prove a run happened.
   - Done when: `gh attestation verify` confirms a receipt came from its workflow run.
3. **A pre-commit hook.** Add `.pre-commit-hooks.yaml` so pre-commit users get `qed check --speed fast` in three lines of config. Your README already says check is safe inside hooks.
4. **MCP Registry, updated by every release.** After PyPI, put `<!-- mcp-name: io.github.AdityaTummuri/qedcode -->` in the README, which the registry looks for on the PyPI page ([package rules](https://modelcontextprotocol.io/registry/package-types)). Create `server.json` with `mcp-publisher init`, then publish from the release workflow using GitHub OIDC. Add one-time listings in community MCP directories and awesome lists.
   - Done when: a release updates the registry entry with no manual step.
5. **Skill marketplaces.** Submit the Agensi listing you already drafted, keep the Claude Code plugin entry, and add the Phase 1 numbers to the Skill README.
6. **Hugging Face.** Publish the bug zoo and `benchy-core` as a small dataset whose card links back. ML people search there, and it needs no upkeep.

## Phase 5: signature engine features (weeks 4-8, then one a month)

Build one shared piece first, a mutation engine, because it makes grade A stronger and powers three of your planned features. Then add the features in this order.

Why mutation first: fail-before/pass-after proves a test noticed the bug, not that it pins the behaviour. A repro test that only asserts `result is not None` can fail before and pass after, yet accept almost any fix.

1. **Mutation-backed proofs.** A new `verify/mutate.py` makes small mutants of only the changed lines (flip `<` and `<=`, `+ 1` and `- 1`, `and` and `or`, return `None`) with `ast`, or tree-sitter for JS/TS, then re-runs only the repro test on each. The receipt gains `mutation: {killed, total}` and the summary says "proof strength 8/9". It is deterministic and cheap: a handful of mutants against one test file. Keep the grade rules unchanged at first; show strength beside the grade, and decide later whether a weak proof should cap at B.
   - Done when: every bug-zoo grade A reports a mutation score, and a deliberately weak repro test scores low and is flagged.
2. **Safety Net Builder (`qed tests`), your planned next step.** Find code no test covers, write tests that pass on today's code, and keep only the tests that kill mutants: that filter separates real tests from padding. A coverage number would add coverage.py, so list it in AGENTS.md first, or ship with mutation scores alone.
   - Done when: on a real untested module, the kept tests pass, kill most mutants and are listed in a receipt.
3. **Smart Questions ("A or B?"), the most novel idea.** When two candidates pass every test but differ, run both on generated inputs (boundary values first, property-based inputs later) until they disagree, inside the candidate worktrees under the same rules as tests. Then ask one question: "For `paginate([], 1, 10)`, should it return `[]` (fix 1) or raise `ValueError` (fix 2)?" The answer becomes a new test, so the ambiguity is settled with proof, not a guess.
   - Done when: on a planted ambiguous task, two passing candidates yield one concrete question, and the chosen answer becomes a test only the chosen fix passes.
4. **Bug Hunter (`qed hunt`).** The model proposes suspicious behaviours and writes tests for them; a finding survives only if its test fails on today's code and a fix for it reaches grade A. You still confirm the expected behaviour, because a test can encode a wrong expectation. Benchy can measure it honestly: `tools/plant_bugs.py` plants bugs with known answers, so you can report how many it finds and how many of its reports are right.
   - Done when: on planted-bug projects, its hit rate and precision appear in a Benchy report.
5. **Night Shift (`qed night`).** Queue tasks before bed (a task list, failing tests, exported issues); your GPU works through them in quiet mode; the morning brings a local HTML report built from the Receipt Viewer, one card per task. Nothing touches your files until you approve. Tagline: "Your GPU works the night shift."
   - Done when: ten queued bug-zoo tasks run unattended and produce one morning report.
6. **Proof-carrying commits.** `qed apply --commit` adds trailers (`QED-Grade: A`, `QED-Receipt: sha256:...`) and stores the receipt in git notes, so the proof travels with the repository and `git log` shows grades. Nothing to host.
   - Done when: `git log --notes=qed` shows the receipt of an applied fix, and the GitHub Action can read it.
7. **Later, only if people ask:** `qed ui` (a local page reusing the website's components), Big Brain mode, Deep check, an in-browser solve with a WebGPU model on the website, and the stop-or-continue judge once Benchy has enough logged runs.

## Phase 6: the story (weeks 5-6)

A portfolio piece is judged by the story around the code, and you have three strong ones: a research finding, an engine built by AI that refuses to trust AI, and a benchmark that caught an agent looking up answers.

1. **A study: "How often do AI coding agents weaken tests?"** Collect public pull requests that declare AI authorship (an AI co-author trailer, or PRs opened by agent bots) and run QEDCode's tamper guard on their diffs: skips added, asserts removed, always-true asserts, test files edited or deleted. It is static diff analysis through the GitHub API: no tests run, no model, no server. Hand-check a sample to measure false positives, publish rates with intervals plus the dataset and code, and report in aggregate without singling anyone out. It gives the GitHub Action its reason to exist, backed by data.
   - Done when: the write-up has its sample size, rates with intervals, a measured precision and a reproduce script.
2. **The case study: "Built by AI, proven by tests."** You directed AI agents to build a tool that refuses to trust AI agents, under the same discipline: a written brief (AGENTS.md), a progress log (PROGRESS.md), "done when" checks, 1,248 tests and CI on three systems. The git history shows first commit to v1.1 in days. Then Benchy found real engine bugs, and each fix arrived with a regression test. Directing AI to build serious software, and verifying it, is exactly what reviewers want to see.
   - Done when: a site page with the timeline, a brief excerpt, three bugs found and fixed, and what you would do differently.
3. **Five posts**, on your site and cross-posted to dev.to:

| Title | Hook | Source material |
| --- | --- | --- |
| My benchmark caught the AI agent googling the answers | The pilot's 8 web searches on a public SWE-bench issue, and the hook that now blocks them | Benchy pilot README, methodology |
| Six ways a fix can look green without being fixed | Test-tampering patterns and how each is caught | `verify/guard.py`, Fool the Grader |
| Receipts for AI code | A re-runnable proof format, offered as an open schema | `verify/receipt.py`, Receipt Viewer |
| Threat-modelling an AI coding tool | A crafted receipt passing `--basetemp`, git hooks, `GIT_DIR`, option-like paths | The hardening pass in PROGRESS.md |
| Can a 4B model with checks beat a 9B model alone? | The Phase 1 numbers, losses included | Benchy reports |

4. **Decision records.** Turn reasoning already written in PROGRESS.md and Benchy's "Revisions" into short records in `docs/adr/`: the model never runs commands, receipts are data, Docker is optional, no submission server, Terminal-Bench rejected, GPL to Apache-2.0. Reviewers read these as senior judgment.
5. **A two-minute video.** Problem, ladder, a real local fix, the Receipt Viewer link, the Action catching a cheating PR, the numbers. Script the terminal parts with VHS so they re-render; record the rest with OBS; host on YouTube and embed on the site.
6. **Launch week**, once Phases 1-4 are live: Show HN ("QEDCode: grade AI code changes A-D with a re-runnable proof"), r/LocalLLaMA led by the 4B-vs-9B result, r/Python, the first post, a LinkedIn post with the video, and an X or Bluesky thread with the GIF. Answer every comment on day one and fold the questions into the FAQ. Have the "why not just use Claude Code?" answer ready: it is a tool those agents call, not a rival.
7. **Profile and resume.** A GitHub profile README and LinkedIn Featured entry. Resume lines, with X, Y and N filled in from Phase 1:
   - Built QEDCode, an open-source verified coding engine (Python, \~14k lines, 1,200+ tests, CI on three OSes) that grades AI code changes A-D with re-runnable proof receipts.
   - Raised a 4B local model's verified fix rate from X% to Y% (95% CI, n=N), measured with a benchmark I designed around hidden tests and paired significance tests.
   - Closed receipt-injection and git-environment attack paths in a threat-modelled hardening pass.

## Phase 7: set-and-forget maintenance (30 minutes a month)

Aim for a project that keeps working for six months untouched and needs 30 minutes a month when you do look.

**Automate once**

- Dependabot or Renovate (both free) for Actions versions and Python dependencies, grouped into one weekly PR that CI judges.
- A weekly scheduled CI run on QEDCode against the newest allowed dependencies (`uv sync --upgrade` in CI only, lock file untouched). Upstream breakage then turns a badge red before a user hits it. The Skill and Benchy repos already do this.
- One tag push does everything: PyPI, GitHub Release, website rebuild, MCP Registry update.
- CI regenerates screenshots, GIFs (VHS with the scripted model), stats and the leaderboard.
- Let `qed models use` take any Hugging Face GGUF or Ollama tag, so a new model month needs no catalog release from you.

**People, without a community manager**

- Issue templates that ask for `qed doctor` output, version and OS; questions go to Discussions, not Discord.
- A `CONTRIBUTING.md` that says what you already practise: read AGENTS.md (and have your AI read it), every change carries a test, run the full gate.
- `CODEOWNERS`, `SECURITY.md` and a standard code of conduct.
- A `MAINTAINING.md` written as prompts you can hand to an AI agent:
  1. Merge the dependency PR if CI is green.
  2. Triage new issues: reproduce from the doctor output, label, answer or close against "What not to build".
  3. Glance at the weekly CI of all three repos.
  4. Each quarter: bump the llama.cpp tag, re-verify the catalog checksums, and rerun the Phase 1 smoke run to catch quality drift.

Done when: a month passes with only that routine, and nothing that can go stale is kept by hand.

## What not to build

Each of these would look impressive for a week and then cost you upkeep forever.

| Tempting idea | Why not | Do this instead |
| --- | --- | --- |
| A hosted web app that runs solves | Servers, bills, abuse, uptime | The CLI, in-browser demos, and the Action on the adopter's CI |
| Accounts, telemetry, usage analytics | Breaks the privacy promise and needs a backend | Benchy's opt-in pull requests; GitHub's traffic insights |
| A VS Code extension | Marketplace and API churn | MCP through `qed connect vscode`, already built |
| A Discord server | Daily moderation | GitHub Discussions |
| Go, Rust or Java now | Each language needs runners, reporters, guards and tests | Wait for demand; Python and JS/TS cover most agent work |
| A bot that submits Benchy results | A privileged token and abuse controls; you already rejected it | Pull requests, as today |
| Chasing every agent's config format | You have already met this churn (Cursor's docs returned 404) | One MCP snippet, plus `qed connect` for the big agents |
| Trained helpers or LoRA now | GPU time, data you do not have yet, ongoing upkeep | Revisit once Night Shift has collected receipts |

## Timeline at a glance

About seven weeks to launch-ready, then a signature feature a month. You build fast, so treat this as the order, not a deadline.

&#91;embedded content: QEDCode roadmap · 8 phases, a gate and launch day, Nov 2026 to Jan 2027\]

Phases overlap on purpose: the benchmarks run overnight while you build the site. Launch waits for the four numbers and lands after the holidays, on a Tuesday.
