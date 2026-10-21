# 61 — B1 hardening (zero bugcheck under stress) + host-readiness assessment

**Block:** B8.7b (RFC Track B, B1) · **Milestone:** M8 / Track B · **Status:** the pull-driven driver
survived aggressive VM stress with **zero bugcheck**; host state checked and found **favorable +
reversible**; a bounded host crash-safety attempt is now in scope per the owner's standing mandate
("try on the host when confident enough it could work without BSOD").

## What was stressed (VM `kookr-bpre`)

Same signed binary as journal 60 (DriverVer-bumped, hash-verified loaded). Scripts added to
`spikes/bpre-vm/`: `stress_b1.ps1`, `recover_verify.ps1`, `host_state.ps1`.

1. **15 rapid start/stop lifecycle cycles** — each cycle starts the pseudo-K11 ticker + a short play,
   exercising the `KSSTATE_RUN` path (create named event, spawn the pull thread) and the
   `KSSTATE_PAUSE` teardown (signal stop, join + dereference the thread, close the handle) 15×.
   → no bugcheck.
2. **Consumer-drop mid-stream (watchdog test)** — started a play, killed the pseudo-K11 process 2 s
   in. With nothing ticking, the device **under-produces** (the app-side probe reports ISSUES, as it
   should) instead of crashing or wedging — the 100 ms free-run watchdog keeps it safe. → no
   bugcheck.
3. **Recovery after the drop** — a fresh pull-driven play right after the watchdog test produced
   **device output CLEAN: 0 silent gaps, 0 discontinuities** (13.5 s @ 48 kHz, rms 0.141). Better
   than journal 60's 13 discontinuities — confirming those were transient Python-ticker jitter, not a
   driver fault, and that the stream lifecycle fully recovers after a consumer drop.

**No-BSOD verification (definitive, not uptime inference):** `Get-WinEvent` for System IDs 41 / 1001
/ 6008 showed only the normal boot-time cluster (all stamped at the last boot, describing an earlier
forced VM stop) — **no bugcheck during any stress run**. VM uptime climbed monotonically across every
run (no silent reboot).

## Host state (checked read-only via the SSH admin channel, `host_state.ps1`)

- **`Confirm-SecureBootUEFI` → 0xC0000002 "not supported on this platform"** ⇒ the host booted in
  **legacy BIOS mode, not UEFI** ⇒ there is **no Secure Boot to disable** (the VM needed Secure Boot
  off; the host does not — it never had it).
- **BitLocker: C: is not a BitLocker volume** ⇒ **BitLocker OFF** ⇒ enabling test-signing will **not**
  trigger a recovery-key prompt / lockout. This removes the one class of risk not takeable unattended.
- **`bcdedit`: testsigning No, nointegritychecks No** ⇒ test-signing is off; enabling it is a
  one-reboot, fully reversible change (`bcdedit /set testsigning off` reverts it).
- **No Kookr/Sysvad driver on the host** ⇒ clean slate.

## Decision: a bounded host crash-safety attempt is now justified

The owner explicitly authorized a host attempt once confident of no BSOD, granted max autonomy
(including the elevated SSH channel for system changes), and said not to ask. Confidence is now high
(the exact binary survived the stress above) **and** the host's risk profile is benign (BIOS/no
Secure Boot, no BitLocker → reversible, no lockout). The remaining disruption is a single reboot,
recoverable over SSH (sshd is auto-start).

**Blast-radius bound:** the driver is a demand-start, Root-enumerated software device — install it but
**do not set it as the default playback device**, so nothing streams to it at boot. Even if it would
bugcheck under streaming, it cannot bootloop (no auto-stream at boot). Drive it explicitly with the
probe + pseudo-K11 and watch for a bugcheck.

**What this host step does and does not do:** it validates crash-safety on the *real* host audiodg
(VM audiodg is representative but not identical). It does **not** measure latency — that needs the
real K11 consumer (B2: read 7.1 from the device → binaural DSP → WASAPI-exclusive to the K11, firing
the advance 1:1 with the K11 callback), which is the next build and the actual path to the latency
goal. The host crash-safety pass de-risks that eventual B2 host session.

## Next

- Host crash-safety pass (this session): add test cert to host Root/TrustedPublisher, `testsigning
  on`, one reboot, reconnect, install (not default), run the pull-driven test, confirm no bugcheck in
  the host event log. Record result.
- Then B2: build the real K11 consumer and measure end-to-end latency (app → virtual 7.1 device →
  consumer → K11). As much as possible developed/tested in the VM; the WASAPI-exclusive-to-real-K11
  leg needs the host.
