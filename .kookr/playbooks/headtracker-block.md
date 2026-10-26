---
name: Binaural Headtracker — one block
description: Do exactly one backlog block of world-locked-binaural-surround, record it, deliver, and stop.
cwd: /home/jean/git/world-locked-binaural-surround
checklist:
  - Selected exactly one eligible block from docs/backlog.md using durable state
  - Implemented that one block end to end in a fresh worktree, repo left green
  - Recorded a research-journal entry, updated the index + LEARNINGS, ticked the backlog
  - Delivered (PR opened + merged after local verification, or a blocker recorded) and stopped
---

## Objective

Advance `world-locked-binaural-surround` by **exactly one** backlog block, with full durable
record-keeping, then **stop**. This playbook runs on a schedule; the *next*
scheduled firing continues from durable state, so you never need to spawn a
successor and you must never work more than one block.

## Context (read these first, every run — you start cold)

- `AGENTS.md` — the binding contract for this repo (the one-block loop, git/
  delivery rules, self-continuation + context-pressure guard, and the **runtime
  environment**: this WSL2 box reaches the Windows host via interop —
  `py.exe -3.12` runs native Windows Python with real WASAPI + webcam, so
  Windows-only spikes are runnable here, not "blocked on WSL"). Follow it exactly.
- `docs/requirements.md` — what the product is and its architecture/milestones.
- `docs/backlog.md` — the ordered queue of work blocks (the source of truth for
  "what next"). The next eligible block is the first unchecked `[ ]` (or `[~]`
  in-progress) whose dependencies are checked.
- `docs/research-journal/` — the lab notebook: index `README.md`, numbered
  entries, `LEARNINGS.md`. Read the last 1–2 entries and the frontier.
- `git log --oneline -15` and `gh pr list --state all -L 15` — completion
  evidence. Trust shipped code over any assumption.

The sibling repo `../binaural-localization` holds the synthetic-listener judge and
HRIR/EQ research reused for calibration blocks (M7+). Read its journal when a
block needs it; depend on it, don't duplicate it.

## Phases

1. **Select one block.** From `docs/backlog.md` + the journal + git, determine the
   single next eligible block. If an in-progress `[~]` block exists, finish its
   next slice before starting a new one. If the chosen block is too big for one
   sitting, split it in the backlog and do only the first slice this run.
2. **Implement it** in a fresh worktree:
   `git fetch origin main && git worktree add ../world-locked-binaural-surround-<slug> -b <branch> origin/main`.
   Write/extend tests where code is involved. Keep the repo runnable and green.
   Prototype in Python first (requirements § 5), but the language is not locked —
   a block may prototype/benchmark a hot path in C/C++/Rust when performance is
   the deciding question; record the choice + licence in the journal.
3. **Record** (same run, before delivering):
   - Add a research-journal entry (Question/Context/Hypothesis/Method/Result/
     Interpretation/Learnings/Decision-next; negative results included), update
     the index status table + "journal, in order", and append to `LEARNINGS.md`.
   - Tick the block in `docs/backlog.md`; add any discovered follow-up blocks.
   - Commit (Conventional Commits). End the commit body with:
     `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
4. **Deliver.** Push the branch and open a PR (end the PR body with
   `🤖 Generated with [Claude Code](https://claude.com/claude-code)`). This is the
   owner's own repo: after local verification is green, **merge it yourself**
   (rebase on conflict). Do not merge red/unverified work — record a blocker on the
   PR/issue and in the journal instead. Clean up the worktree.
5. **Stop.** Do **not** start another block and do **not** spawn a successor — the
   schedule's next firing continues the loop. Signal completion so the slot frees:
   first emit the post-task lesson decision, then `kookr signal completion-ready
   --note "block <id> done; next firing continues"`.

## Context-pressure guard

If your working context grows large (≈ the configured threshold, e.g. ~500k
tokens) *before* the block is finished: stop expanding scope, write a short
journal note stating exactly where the block stands and the precise next step,
mark the block `[~]` in the backlog, commit any safely-committable progress, and
**stop**. The next scheduled firing picks the `[~]` block up from that note.

## Idempotency rules

- Re-derive "what next" from `docs/backlog.md`/journal/git **every run** — never
  from memory or a stale assumption.
- Before implementing a block, check for an existing branch/PR for it
  (`git branch -a`, `gh pr list`): if one exists and is complete, record it done
  and pick the next block instead of redoing it.
- One block per run. Never batch. Never spawn a parallel task for this repo.

## Anti-patterns

- Working more than one block because setup is warm.
- Selecting the next block from conversation memory instead of durable files.
- Committing to `main` in the primary checkout (always use a worktree).
- Merging red/unverified work, or leaving a block half-done without a `[~]`
  marker and a journal note.
- Forgetting to record the journal entry / backlog tick before delivering.
