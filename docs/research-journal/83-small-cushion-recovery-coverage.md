# 83 — Small-cushion recovery and the limits of timing margin

## Question and context

After PR #216 the owner confirmed that block 256 with three or four cushion blocks works
with nearly no underruns. The previous pipeline regression used a 40 ms cushion: eight
blocks at 256, rather than the owner's three (16 ms) or four (21.33 ms). What survives at
those smaller settings, and which failures require more margin rather than a recovery fix?

## Hypotheses and method

1. Correct capture service should recover with small cushions after a bounded stall, without
   relying on a track gap. Test real production capture service, eight-channel convolution,
   FIFO re-prime, PI drift correction, and rubato output interpolation.
2. Device requests larger than the available timing margin can still exhaust a small cushion.
   Retain an explicit failing boundary instead of making every modeled schedule pass.
3. Slow clock correction cannot replace fast FIFO re-prime. Measure its settling separately
   with an ideal continuous-frame plant, keeping packet jitter out of that experiment.

Extend the portable pipeline with a capture ring bounded by frames (60 ms), rather than
packet count; splitting a packet must not change its storage capacity. Use independent
capture/output clocks with opposite ±200 ppm offsets, 480-frame packets or 96+384-frame
fragmentation, and endpoint requests of varying sizes paced by exact frame counts. Cover
44.1/48/96 kHz output, three/four-block cushion targets, 256/512/1024 block changes, a
cushion-only change, and five capture stalls per scenario. Preserve the convolution state
on a cushion-only transaction. The 96 kHz cases allow 960-frame requests; supported lower-rate
cases use requests up to 480 frames.

Measure both FIFO recovery and resumed output signal after each stall. Require one underrun
and re-prime per forced fault, no additional underruns after a 150 ms settling deadline,
finite bounded samples, bounded pending output, exact endpoint-frame consumption, and
correct per-ear tone frequencies (440/880 Hz), energy, and low cross-correlation after each
configuration phase. These signal metrics detect mute, ear swaps, duplicated ears, and gross
rate errors; they are not a full distortion or perceptual-quality measurement.

Add separate packet error/arrival-race tests. Distinguish clocked silent packets from a
220 ms no-packet capture gap, below the production 250 ms idle-silence feeder threshold.
The long-idle feeder itself remains outside the integrated model; the existing unit test
checks the output stage's idle flag and re-prime freeze conditions.

Extend the Windows capture-only probe to report per-window read-gap p95/max and largest
batch. Extend its host gate to accept different cushion latency targets. Run against the
real VoiceMeeter source at block 256 with 16/21.33 ms cushion targets and 80/300 ms capture
stalls. No output device is opened; no live settings or routing are changed.

## Results

### Portable production-pipeline scenarios

All six supported scenarios recovered without a track gap. Each observed exactly five
underruns and five re-primes, matching the five deliberate faults. Maximum time from the
end of a stall to an output event with resumed signal was:

| Output rate | Initial cushion blocks | Stall per fault | Maximum signal recovery |
| --- | --- | --- | --- |
| 44.1 kHz | 3 | 80 ms | 14.42 ms |
| 44.1 kHz | 4 | 180 ms | 44.90 ms |
| 48 kHz | 3 | 80 ms | 18.23 ms |
| 48 kHz | 4 | 180 ms | 42.96 ms |
| 96 kHz | 3 | 80 ms | 30.12 ms |
| 96 kHz | 4 | 180 ms | 44.52 ms |

These are deterministic scenarios, not a controlled comparison of cushion sizes: clock
sign, packet fragmentation, stall duration, and block size also vary. A resumed-signal event
is an energy threshold observation; the post-recovery waveform checks and absence of later
underruns provide separate evidence of sustained output.

The boundary scenario deliberately admits a 960-frame output request at 44.1 kHz. That
represents 21.77 ms of demand, exceeding the 16 ms margin of three 256-frame blocks. In this scenario it
produced an additional late underrun despite correct capture draining. The test preserves
this limitation. It is not evidence that the owner's K11 makes requests of this size.

### Real Windows capture

The four small-cushion host cases passed. Final measured capture windows ranged from
47,971 to 48,025 Hz with no new discontinuities. Outside the forced pauses, read-gap p95
was 10.39–10.65 ms and maximum read gap was 10.88 ms in the recorded windows. Batches were
normally 480 frames; after a 300 ms forced pause the largest reported batch was 1,056 frames.
The host gate also rejects the retained old single-packet backend at block 256 / 16 ms /
80 ms stall, preserving an independent hardware negative control.

The probe's FIFO counters are a coarse wall-clock model serviced after capture reads;
they do not measure asynchronous DAC underruns and must not be compared with cockpit
underruns. The host gate evaluates capture rate and discontinuity recovery only.

A 25-second live cockpit sample at the owner's four-block setting had FIFO snapshots of
one to four blocks, drift correction from −3,584 to +2,305 ppm, and two new capture
discontinuities. The rolling dropout meter went from one to none, which cannot prove no
new events occurred because older events expire. A separate 12-second capture-only run
was near 48 kHz throughout, with no post-start discontinuities and a maximum read gap of
11.1 ms. These intervals are observations of a changing live workload, not a controlled
causal comparison.

### Slow drift controller

An ideal continuous-frame simulation runs 15 virtual minutes per clock-drift direction,
using the production gains and three/four-block setpoints. No case exhausted the FIFO's
modeled range. The time after a step until the correction stayed within 10 ppm was:

| Block | 3-block target: +200 / −200 ppm | 4-block target: +200 / −200 ppm |
| --- | --- | --- |
| 256 | 58.62 / 71.76 s | 75.83 / 91.21 s |
| 512 | 117.24 / 143.52 s | 151.67 / 182.42 s |
| 1024 | 234.47 / 287.04 s | 303.34 / 364.84 s |

The negative step follows the positive step, so it is a 400 ppm reversal. An initial
600-second horizon was insufficient for the strict 1 ppm final-error assertion in the
1024/four-block reversal case. Extending the virtual horizon to 900 seconds verified
convergence without changing gains or error tolerances. Settling is proportional to block
duration in this ideal plant because the controller gains are per update, not per second.
This does not measure convergence under the real block-quantized FIFO and event timing.

## Decisions and next step

Keep the current capture/re-prime policy and drift gains. Three/four-block operation is now
covered with meaningful recovery and signal assertions. Larger cushions buy timing margin;
none can guarantee clean playback through arbitrarily delayed device service. The tests do
not establish a universally optimal cushion or justify adaptive buffer growth.

The next useful improvement is a bounded diagnostic trace around actual underruns: capture
read gaps/batch sizes/discontinuity flags, render-batch wall time, output frame requests/wake gaps, FIFO fill,
and re-prime transitions on the same timeline. That would help identify or narrow down capture loss, DSP
service delay, and output demand bursts before changing scheduling or buffer policy.
The next work is scoped in [issue #217](https://github.com/jeanibarz/world-locked-binaural-surround/issues/217),
with separate instrumentation and host-validation slices. Preserve the owner's listening setup when collecting it.

No engine runtime policy or new dependency changes in this block. The new host probe is
available independently; the owner does not need to restart the working engine for these tests.

## Verification

- `cargo test --manifest-path rust-engine/Cargo.toml -- --nocapture`
- `cargo test --manifest-path rust-audio-io/Cargo.toml -- --nocapture`
- Cross-build `audio_probe` for Windows and run the host gate at block 256 with
  `--latencies-ms 16 21.333333 --stalls-ms 80 300`.
- Results: 54 engine tests passed (two existing tests ignored); 21 audio-I/O tests passed.
- Windows probe release cross-build and Windows engine cross-check passed.
- Review and delivery PR recorded after verification.
