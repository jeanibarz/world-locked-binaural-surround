# 62 — What Track B actually buys (latency budget) + host attempt staged

**Block:** B8.7b (RFC Track B) · **Milestone:** M8 / Track B · **Status:** the host crash-safety
attempt is fully staged + reversible (can't run this session — see below); the virtual device's
latency floor is characterized and reconciled against the host baseline (journal 48), which sharpens
what Track B wins.

## The latency budget — Track B wins the `queue` term, not the `device` term

Journal 48 measured the live host (VoiceMeeter) path and split audio latency into:

- **`device` ≈ 22 ms** — WASAPI *shared-mode* buffering (audiodg's mixer: ~2× the 10 ms default
  period). Constant throughout.
- **`queue` ≈ 16–20 ms** — the passthrough app's output-FIFO *cushion* that absorbs jitter between
  the capture-burst-paced worker and the K11 output callback. Floored at ~2 blocks (≈21 ms @ block
  512); lowering `--latency-ms` below the floor does nothing.

**VM probe of our own SYSVAD device (`latency_probe.py`, idx = "Virtual Audio Device (WDM) - Tablet
Sample"):** PortAudio realises a **22 ms** shared-mode stream latency — **the same `device` term**.
PortAudio's optimistic `default_low_output_latency` is 3 ms, but shared-mode audiodg clamps the
realised figure to ~22 ms.

**Consequence (the honest reframing of "latency gone"):**

- The **`device` ≈ 22 ms** term is **inherent to any shared-mode virtual device** — VoiceMeeter *and*
  our SYSVAD device alike — because arbitrary playback apps (games/video/music, the owner's "a bit of
  everything") open the endpoint in shared mode and get audiodg's mixer buffering. Track B does **not**
  remove it. (It is only reducible if the *source* apps opt into IAudioClient3 low-latency, which we
  can't force on games.)
- The **`queue` ≈ 16–20 ms** term is exactly what Track B's pull-driven design removes: the consumer's
  output (K11) callback pulls from the virtual device **1:1 with the K11 clock**, so there is no async
  worker → no cushion FIFO → no `queue` term (and no cushion jitter). This matches A0 (the term is
  jitter/burst, not skew) — synchronizing to the K11 clock is the right lever, a resampler is not.

**So the expected Track-B win is ≈ 16–20 ms of audio latency + its jitter** — real and worth having,
but not "all of it": the 22 ms shared-mode `device` term and the ~40 ms camera/pose stale remain the
larger floors. This should temper the "latency gone" framing to "the one removable audio term, gone."

## Host crash-safety attempt — staged, reversible, but can't run this session

**Why not this session:** this WSL/Claude session runs *on the host*. Loading a test-signed kernel
driver requires test-signing mode, which requires a **reboot** — which restarts WSL and kills this
session (no auto-resume). So the host step is necessarily a handoff to a post-reboot session.

**Everything is verified ready (journal 61 + this one):**
- Host is legacy BIOS (no Secure Boot) + BitLocker OFF → `testsigning` is reversible, no lockout.
- The host package `D:\kookr-vm\pkg` `.sys` is **byte-identical** (sha256 `A021A1DE…B29D`) to the
  binary stress-tested in the VM (staged *and* installed copies match) → the host runs the validated
  driver.
- x64 `devcon` present (`…\Tools\10.0.26100.0\x64\devcon.exe`); `pnputil` present; signing cert in
  `CurrentUser\My` (import to Root/TrustedPublisher round-trips cleanly); sshd is auto-start
  (channel survives the reboot); host Python 3.12 present (audio deps installable).

**Turnkey scripts (in `spikes/bpre-vm/` and on the host `D:\kookr-vm\`):**
1. `host_arm_testmode.ps1` — trust the cert + `testsigning on` (no auto-reboot).
2. *(reboot — operator's deliberate choice)*
3. `host_install_and_test.ps1` — install the device (demand-start, **not** default → cannot bootloop
   even on a streaming crash), confirm the loaded hash, check the event log for a bugcheck, write
   `D:\kookr-vm\host_test_result.txt`.
4. `host_revert.ps1` — remove device + package, `testsigning off`, drop the cert. Host back to stock.

No auto-run-on-boot anywhere (no bootloop risk); every step is deliberate and logged.

## Next

- **B2 (the latency payoff):** the real K11 consumer — loopback-read the virtual device → binaural DSP
  → **WASAPI-exclusive to the K11**, firing the pull tick 1:1 from the K11 render callback, with **no
  cushion FIFO**. That is the construct that removes the `queue` term; measure the end-to-end audio
  latency against the journal-48 baseline to confirm the ≈16–20 ms win. The output-exclusive-to-K11
  leg needs the host; the loopback-read + tick-from-callback loop can be dry-run in the VM.
- The host crash-safety pass (scripts above) is a quick prerequisite for B2 and de-risks the install.
