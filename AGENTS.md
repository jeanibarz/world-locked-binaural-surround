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

## Working discipline

- Prototype in Python first for fast iteration; move hot paths native later
  (see requirements § 5). Keep config/HRIR/world-model shared across both.
- One variable at a time for any comparison; state the hypothesis before the
  result; record negative results. (Mirrors the sibling project's method.)
- Keep large binaries (SOFA, wav, models) **out of git** (see `.gitignore`);
  document how to fetch/rebuild them.
- When unsure between a small implementation and a larger design question, if the
  block is implementation, implement; if it is a genuine architecture fork,
  record an RFC-style journal entry proposing a direction and proceed with the
  best-justified option rather than stalling.

## The sibling project

`../binaural-localization` is the research project building the **synthetic-listener
judge** and HRIR/EQ findings this project reuses for calibration (FR-10/11). Read
its `docs/research-journal/` when working calibration blocks; do not duplicate its
work — depend on it.
