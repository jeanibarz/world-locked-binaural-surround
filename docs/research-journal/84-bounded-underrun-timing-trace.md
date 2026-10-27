# 84 — Bounded timing evidence around underruns

## Question and hypothesis

PR #218 covered small-cushion recovery and demonstrated a timing boundary, but aggregate
live telemetry still could not identify the stage behind rare remaining underruns.
Issue #217 asks for capture, render, output, and FIFO timing on one monotonic timeline.

Hypothesis: retaining a bounded pre/post window around actual FIFO underruns may show
whether a capture gap, render-service delay, output wake gap, demand burst, or graph rebuild
coincided with the event. Correlation can narrow the investigation; it is not proof of a
single cause, and multiple signals may overlap.

## Implementation

`--audio-trace <path>` enables a shared monotonic recorder. The capture thread emits read
intervals, blocking-read duration, frame counts/flags, render-batch wall time, and successful
reconfiguration duration. The output thread emits serviced wake intervals, requested frames,
FIFO snapshots, and pull/re-prime transitions. Source rate and initial settings are metadata.
A shared block-size snapshot is updated under the FIFO lock so output context agrees with
live block-size changes.

Audio threads submit copyable events to a bounded channel with `try_send`. A separate
collector retains 250 ms prehistory and 1,000 ms post-trigger context, merges nearby triggers,
sorts retained observations by timestamp, and writes JSONL. Storage and file-row caps plus
loss/late-arrival counters make missing observations visible. Startup creates and flushes
the header before audio starts. Write errors disable recording and report an error from
the worker; normal shutdown joins output before finalizing the recording.

The summary separates events observed before/at the trigger from later completions whose
measured interval overlapped it. Capture overlap uses the maximum of gap and read duration,
not their sum (the gap already includes the wait). Partial-input render batches get at
least one block of timing budget so a harmless zero-produced-frame batch is not flagged as
over-budget. A reconfiguration pause is reported separately from normal render service.

The portable report tool validates the format, preserves the two evidence groups, reports
window maxima and FIFO transitions, and warns about incomplete recordings/loss. It does not
turn a resumed FIFO block into an audible-recovery claim. Its trigger-count check excludes
older underruns present only as prehistory from a previous incident.

No new third-party dependency or audio buffering policy was introduced.

## Tests and measurements

Recorder tests cover pre/post context, simultaneous signals, delayed completions, timestamp
reordering, overlapping triggers, storage/file caps, saturation, shutdown flushing, writer
failures, and disabled state. A virtual-pipeline test drives the production capture policy,
convolution, FIFO, output resampler, and recorder through 80/180 ms capture-worker stalls.
It verifies actual underrun/re-prime/recovery events and capture-gap evidence after the
trigger. It does not verify the Windows instrumentation wiring.

| Virtual worker stall | FIFO trigger | Recorded capture-delivery gap | Resumed signal |
| --- | --- | --- | --- |
| 80 ms | 1,010 ms | 90 ms | 1,080 ms |
| 180 ms | 1,010 ms | 190 ms | 1,180 ms |

The first target-OS producer probe ran 4,096 paired disabled/enabled submissions with a
concurrent collector and one synthetic incident. No audio device was opened. Queue drops,
truncation, and late-arrival counts were zero. Enabled p95 was 8.3 µs, p99 12.4 µs, maximum
175.2 µs. Disabled p95 was 0.4 µs, p99 0.8 µs; its median fell below the timer's resolution.
These are observations under the current host load, not hard scheduling guarantees or a
measurement of the full engine's added FIFO snapshot/timing overhead.

The Rust recorder's earlier optimized saturated-queue microbenchmark measured ~52 ns per
enabled call, which is a different failure path and excludes collector cost. It must not
be substituted for the successful-submission Windows distribution above.

## Verification and host follow-up

- Engine suite: 70 passed, three ignored (two existing reference tests and the manual
  producer microbenchmark). Audio-I/O suite: 21 passed. Report parser: eight tests passed.
- Windows engine/probe release builds and raw-report interoperability are checked locally.
- The reviewed engine was deployed and restarted gracefully on the Windows host, preserving
  the live block 512, 32 ms cushion, timealign method, headphone EQ, and camera settings.
  Source/output remained VoiceMeeter Input/K11 at 48 kHz. No routing or device defaults changed.

### Live Windows observation

A 60-second SSE observation collected 1,055 state samples. The last-60-second dropout meter
reported zero underruns throughout. The lifetime discontinuity count stayed at two; it did
not increase during this observation. Sampled FIFO fill ranged from
one to four blocks (median three); sampling can miss shorter excursions. Render load ranged
from 15.6% to 28.3% (median 21.0%), with the reported peak reaching 45.6%. Drift correction
ranged from −1,933 to +2,717 ppm during this post-startup observation; this is not a settled
clock-mismatch estimate.

The live trace contained its header and no admitted incidents. This observation is
**inconclusive about rare remaining underruns**: there was no event to classify. The engine
remains running with tracing enabled. Its live file has no shutdown footer, so final queue
loss counters are unknown. The separate completed Windows synthetic probe had zero losses;
that does not establish the live engine's loss rate or full tracing overhead. A controlled
full-engine on/off overhead comparison remains unmeasured.

Host artifacts, outside git: `C:\Users\ibarz\WLBS-Engine\traces\live-20261027-011355.jsonl`,
`live-telemetry.jsonl`, and `live-session.json` in the same directory. Deployed executable
SHA-256: `740fcdcc115f1a7b16dc58ab86b1824dee6374913fe2e06242c3e565bb0a5e7d`.
The artifact was built from code commit `04784d1`; later delivery edits update documentation only.

## Decision

Use this opt-in evidence to choose subsequent robustness work. Do not infer a causal fix
from low render CPU, a single flagged threshold, an intentional source pause, or a synthetic
probe. User-facing collection/readback instructions are in `docs/audio-timing-trace.md`.
