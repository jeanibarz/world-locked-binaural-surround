# Research journal — a head-tracked, world-locked binaural renderer

The linear, chronological record of this project: the problem, the objective,
every block we build, what each one taught us, where we were wrong, and what we
do next. Read top to bottom for the whole story; jump to any entry for one step.

Written as a genuine engineering/lab notebook: state intentions before results,
record negative results, tie conclusions to evidence, write for a cold reader.
It is maintained by an autonomous build chain (see [`../../AGENTS.md`](../../AGENTS.md)).

## The problem & objective

See [`00-problem-and-objective.md`](00-problem-and-objective.md). In short: static
headphone surround rotates with the head and fails to externalise; we keep the
virtual speakers **world-locked** and move the convolution with the head, to train
real-space perception — as a low-latency Windows app with a virtual 7.1 device,
individual/AI-tuned HRIRs, live EQ, and an AI judge for calibration. Full
requirements: [`../requirements.md`](../requirements.md).

## Status at a glance

| Milestone | Goal | Status |
|---|---|---|
| M0 | Foundations: repo, docs, dev env, spikes | ◑ in progress |
| M1 | Static binaural render engine (7.1 SOFA → stereo) | ☐ |
| M2 | Head-pose tracker (camera → smoothed az/el/roll) | ☐ |
| M3 | World-lock: tracker ↔ render, world-fixed speaker | ☐ |
| M4 | End-to-end on Windows (virtual device → headphone) | ☐ |
| M5 | Live EQ filters & presets | ☐ |
| M6 | GUI | ☐ |
| M7 | AI-judge calibration loop | ☐ |
| M8 | Latency hardening (native hot path) | ☐ |
| M9 | HRIR fine-tuning, packaging, donationware release | ☐ |

The ordered work blocks live in [`../backlog.md`](../backlog.md).

## The journal, in order

| # | Entry | One line |
|---|---|---|
| 00 | [Problem & objective](00-problem-and-objective.md) | Why world-locked head-tracked binaural, and what we are building. |

See also [`LEARNINGS.md`](LEARNINGS.md) — the cumulative, numbered lessons.

## Maintenance protocol (the contract)

After every block or decision: extend the linear record with a new numbered entry
(never rewrite history to hide a wrong turn), update this index's status table and
"journal, in order", keep the frontier honest, and add durable lessons to
`LEARNINGS.md`. Entry template: **Question · Context · Hypothesis · Method ·
Result · Interpretation · Learnings · Decision/next.** One variable at a time;
hypothesis before result; record negative results; tie conclusions to evidence.
