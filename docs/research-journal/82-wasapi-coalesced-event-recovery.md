# 82 — Capture backlog can survive a stall when events coalesce

## Question and hypothesis

The owner reported that switching from block 512 to 256 started underruns, switching back
left them running, and a later track boundary sometimes restored clean playback. A prior
live sample showed a depleted FIFO, drift correction pinned at +4,000 ppm, and roughly
1,000 new capture discontinuities in 30 seconds. High host CPU load was a competing
hypothesis, but did not establish the cause.

Hypothesis: the inline renderer rebuild briefly stops capture service. The capture backend
then waits once and retrieves one packet per event. Windows notifications can coalesce,
leaving multiple packets queued behind a single notification. Waiting for another event
before servicing that queue can sustain a capture deficit after the original stall ends.

## Method

Inspecting wasapi 0.26.0 confirmed that `read_from_device_to_deque` calls `GetBuffer` once;
it does not drain the device queue. Microsoft's [loopback capture example](https://github.com/microsoft/Windows-classic-samples/blob/main/Samples/ApplicationLoopback/cpp/LoopbackCapture.cpp)
explicitly drains every available packet because callbacks need not occur once per packet.

Extend the existing capture-only `audio_probe` with a one-shot capture-thread stall after
three seconds and per-second frame-rate/discontinuity reports. Build the old capture policy
before applying the fix and retain both binaries outside git. Run against the owner's real
Windows VoiceMeeter Input stream (48 kHz, eight channels) through WSL interop. The probe
opens no playback output and changes no routing. Keep the source playing across each run.

Add portable tests sharing the production capture service, FIFO, convolution renderer,
drift controller, and rubato output resampler. Model finite device packet storage, combined
auto-reset notifications, 480-frame/10 ms capture packets, independent output clocks,
180 ms stalls, packet loss, and 512 → 256 → 512 changes. Include the old capture policy as
a negative control under recurring notification coalescing. Check post-recovery underruns,
signal energy, finite bounded samples, bounded output backlog, and frozen drift on idle /
re-prime. Separate packet tests cover mixed SILENT flags, spurious events, pending work
without a new event, and bounded draining.

## Results

A 12-second Windows run of the old backend started near 48 kHz. After a 300 ms stall,
it stayed at 34.9–35.6 kHz with 33–35 discontinuities/second until exit. Its wall-clock
FIFO model counted 53 underruns. The same probe with corrected capture drained the backlog,
returned to approximately 48 kHz, and reported no further discontinuities in the remaining
eight seconds. Its FIFO model counted one underrun around the injected stall.

The repeatable host gate, `spikes/audio_capture_recovery_check.py`, rejected the old
backend (512 / 300 ms). It passed all six corrected-backend cases:

| Block | Injected stall | Capture rate in final measured windows | New discontinuities there |
| --- | --- | --- | --- |
| 256 | 80 ms | 47,990–47,994 Hz | 0 |
| 256 | 300 ms | 47,994–48,010 Hz | 0 |
| 512 | 80 ms | 47,993–48,017 Hz | 0 |
| 512 | 300 ms | 47,980–48,023 Hz | 0 |
| 1024 | 80 ms | 48,002–48,030 Hz | 0 |
| 1024 | 300 ms | 47,971–47,987 Hz | 0 |

Whole-run average rates include the deliberately lost capture interval; they must not be
interpreted as steady clock drift. The host gate requires an active initial source and
near-nominal capture with no new discontinuities after its recovery deadline.

## Change and decision

Service queued capture packets before waiting, and drain available packets per service
call. Apply SILENT handling per packet; combine discontinuity with OR and entirely-silent
status with AND. Limit a batch to 256 packets so continuous arrivals cannot monopolize a
service call; the next call checks queued work before waiting. Keep existing FIFO re-prime
and drift gains. Move the unchanged output resampling/fade logic into a portable module so
regression tests execute the implementation used by Windows.

This establishes a real capture recovery defect and a fix for the reproduced persistent
starvation. It does not establish that every audible glitch has the same cause. The virtual
pipeline uses short synthetic room filters and virtual device clocks; it does not benchmark
the owner's long BRIR, headphone EQ, camera, DAC, or OS scheduling. The Windows probe tests
real capture and a simulated output drain, not acoustic playback. A track boundary's exact
recovery mechanism was not directly instrumented. The existing owner's process was left
running during measurements.

No new dependency was added: the existing audio_io and rubato dependencies are now portable
for tests. No driver, device defaults, or VoiceMeeter routing changed.

## Verification and follow-up

- Packet tests fail with the old service policy and pass with the fix.
- Windows host recovery gate: old backend FAIL; corrected backend 6/6 PASS.
- Run `cargo test --manifest-path rust-audio-io/Cargo.toml` and
  `cargo test --manifest-path rust-engine/Cargo.toml` for deterministic regression coverage.
- Windows engine release cross-build passed.
- Deterministic suites: 18 audio-I/O tests and 50 engine tests passed; two existing engine
  tests remain ignored because they require external reference assets.
- Stage the Windows engine using the existing `.new.exe` update mechanism,
  and confirm the owner's next run picks up that artifact. Listening remains the check for
  audible quality beyond the measured starvation defect. Delivery PR recorded below.

Delivery: [PR #216](https://github.com/jeanibarz/world-locked-binaural-surround/pull/216).
The verified Windows release executable is staged as `wlbs_engine.new.exe` in the owner's
install directory; the existing launcher applies it on the next launch. The currently
running process was not replaced. Issue #215 remains open for listening validation.
