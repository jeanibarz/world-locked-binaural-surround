# AGENTS.md — contract for the autonomous build chain

This repository is built **block by block** by a self-continuing Kookr agent
chain. Each task does **one small, independent block**, records its progress in
durable files, and leaves the repo green. This file is the contract every task
reads at the start of its run. It must stay self-sufficient: a task starts
**cold**, with no memory of previous tasks — everything it needs is here, in
[`docs/backlog.md`](docs/backlog.md), and in
[`docs/research-journal/`](docs/research-journal/README.md).

## What this project is

A real-time, head-tracked, world-locked binaural renderer for Windows. Read
[`README.md`](README.md) for the idea and [`docs/requirements.md`](docs/requirements.md)
for the full requirements and architecture before doing anything else.

## The one-block loop (how each task works)

1. **Read durable state, not memory.** Open `docs/backlog.md` (the ordered block
   list), the latest `docs/research-journal/` entries, and `git log`. Decide the
   single next eligible block from what is *recorded done*, never from assumption.
2. **Pick exactly one block.** The smallest next unit that moves a milestone.
   Prefer unblocking/foundational work. If a block is bigger than one sitting,
   split it and do only the first slice (and record the split in the backlog).
3. **Do that one block end to end**, in a fresh git worktree (see below). Write
   or update tests where code is involved. Keep the repo runnable and green.
4. **Record the outcome** in the same run:
   - Add/append a **research-journal entry** (hypothesis/method/result/decision,
     negative results included) and update its index + `LEARNINGS.md`.
   - Tick/advance the **backlog**: mark the block done, add any follow-ups or
     newly-discovered blocks.
   - Commit with a Conventional-Commits message.
5. **Deliver** per the delivery policy below, then **hand off** (continue the
   chain or stop) per the self-continuation policy below.

Do **one** block per task. Do not batch blocks because setup is warm.

## Durable state = the three files

- **`docs/backlog.md`** — the ordered, checkbox list of work blocks grouped by
  milestone. This is the queue. The "next eligible block" is the first unchecked
  block whose dependencies are checked. Keep it honest and current.
- **`docs/research-journal/`** — the linear lab notebook: one entry per block or
  decision, an index (`README.md`), and `LEARNINGS.md`. This is *why* and *what
  changed*, written for a cold reader. Mirror the maintenance protocol used by
  the sibling project `binaural-localization`.
- **`git` history** — commits and (if a remote exists) PRs are the completion
  evidence. Prefer "a merged change exists" over "I remember doing it".

## Git & delivery

- **Never commit to `main` in the primary checkout.** For every tracked-file
  change, create a fresh worktree:
  `git worktree add ../binaural-headtracker-<slug> -b <branch> main`
  (or `origin/main` if a remote exists). Edit, commit, and push from there.
- Conventional Commits; small, atomic, green commits.
- If a GitHub remote is configured: open a PR per block and, for the owner's own
  repo, **merge after local verification** (tests/build green). Record the PR in
  the journal. If no remote: committing to the feature branch and fast-forwarding
  `main` locally after verification is the delivery step.
- Do **not** merge red, broken, or unverified work. Record a blocker instead.
- Keep every added dependency licence-compatible with a future donationware
  release (prefer MIT/BSD/Apache; flag GPL). Record each dependency + licence in
  the journal.

## Self-continuation (24/7, resilient to kills)

This chain is designed to run unattended and survive a task being killed
mid-block, because **state lives in files, not memory**. Each task:

- Determines the next block **only** from `docs/backlog.md` + journal + git.
  A killed task leaves no half-written durable state if you commit atomically at
  the end of the block (work-in-progress stays on an un-pushed worktree branch
  and is simply re-derived).
- When the block is done and recorded, **hands off**: spawn the next task in the
  chain (same contract, a content-distinct cursor naming the next block), or stop
  if the backlog has no eligible block. Follow the
  `kookr-toolkit:self-continuation-task` skill for the exact handoff + slot-release
  mechanics.
- **Context-pressure guard.** If the working context grows large (≈ the
  configured threshold, e.g. ~500k tokens) *before* a block is finished:
  stop expanding scope, write a short journal note capturing exactly where the
  block stands and the precise next step, commit any safely-committable progress,
  and hand off a successor whose cursor points at *finishing this same block*.
  Never carry unfinished reasoning across the boundary in memory — put it in the
  journal.

## Runtime environment (where spikes actually run)

The dev box is **WSL2 (Linux)**, but it sits on the owner's **Windows** host and
reaches it through **WSL interop** — so Windows-only spikes (audio/WASAPI,
camera) are runnable **from here**, not blocked:

- `py.exe -3.12` launches a native **Windows Python** that has the real audio
  stack (`sounddevice`/PortAudio → WASAPI) and the webcam. Run a repo script by
  its Windows path: `WINPATH=$(wslpath -w <script>); py.exe -3.12 "$WINPATH"`.
- WSL itself exposes only WSLg PulseAudio (no WASAPI, no soundcard), which is
  **not** representative of the Windows low-latency path — never quote WSL audio
  numbers. Keep portable logic in the package (unit-tested on WSL/CI without any
  device), and put hardware-touching code in `spikes/`.
- Do **not** record an audio/camera block as "blocked on WSL" — drive the
  Windows Python. (See journal 02.)

## Working discipline

- **Prototype in Python first** for fast iteration, but the language is **not
  locked**: a block may prototype or benchmark a hot path in **C/C++/Rust** as
  soon as performance is the deciding question — don't defer all native work to
  M8 (see requirements § 5). Record the language choice and its licence in the
  journal; keep config/HRIR/world-model shared across languages.
- One variable at a time for any comparison; state the hypothesis before the
  result; record negative results. (Mirrors the sibling project's method.)
- Keep large binaries (SOFA, wav, models) **out of git** (see `.gitignore`);
  document how to fetch/rebuild them.
- When unsure between a small implementation and a larger design question, if the
  block is implementation, implement; if it is a genuine architecture fork,
  record an RFC-style journal entry proposing a direction and proceed with the
  best-justified option rather than stalling.

## Analysis depth — extract the maximum (NON-NEGOTIABLE)

One experiment, one average, and a couple of aggregate stats is **not** an
analysis — it is the *start* of one. Shallow "ran it, averaged it, done" is the
single biggest failure mode here; catch yourself in it. Before concluding
**anything**, at high effort:

- **Use every tool and knowledge source you have — all of them.** You are not
  limited to a Python REPL. Reach for: the sibling's **synthetic-listener judge**
  (`binaural_localization.second_listener`) and its **pinna-notch toolkit**
  (`binaural_localization.notch`) and DTF/interpolation code; the domain
  **knowledge bases** via `kb search` / the `kb-scout` agent (`hrtf-individualization`,
  `auditory-localization-models`, `sound-source-localization`,
  `experiment-methodology`, …); the research literature; subagents for parallel
  analysis. Leaving a relevant instrument unused is a defect, not a shortcut.
- **Analyse from every angle, never one.** Per-item *and* aggregate; over time
  *and* over frequency; per-channel *and* per-direction; distributions, outliers
  and trends — not just the mean. The mean hides exactly the structure that
  explains the phenomenon (an L/R asymmetry, a per-direction dip, a reverb-tail
  wander all vanish into an average).
- **When you have multiple evaluators/judges, run MANY evaluations and
  triangulate.** Cross-check every claim by an independent method. A conclusion
  only one metric supports is a *hypothesis*, not a finding; several cheap probes
  that agree beat one headline number.
- **Let the ear (and the judge) arbitrate, then find the metric that agrees.**
  If a measurement disagrees with perception, the measurement is the wrong proxy
  — keep digging for the right one (see LEARNINGS L33, the DRR correction).
- Conclude only when the evidence is **exhausted and converges**, and record what
  you **ruled out**, not just what you found.

This is how the research moves toward the correct direction. Depth first.

## The sibling project

`../binaural-localization` is the research project building the **synthetic-listener
judge** and HRIR/EQ findings this project reuses for calibration (FR-10/11). Read
its `docs/research-journal/` when working calibration blocks; do not duplicate its
work — depend on it. It also carries reusable analysis instruments to bring to bear
(per *Analysis depth* above): `second_listener` (localization judge), `notch`
(pinna-notch metrics), `dtf`, and the `eqapo` engine.
