# 74 — The beeps were a period/block mismatch, not tick-stealing; Approach A (user-space pull)

**Block:** B8.7c (RFC Track B, B2, issue #104) · **Milestone:** M8 / Track B · **Status:** host-validated
diagnosis; cockpit integration written (opt-in `--pull-sysvad`), pending the live GUI listen test.

## What journal 73 got half-right

Journal 73's device-level pull (one pull thread advances every registered stream) **was** a real fix —
with a producer + our loopback both in RUN, the balance went from 0.47 (two streams each stealing half
the ticks) back to the single-stream baseline. But on the host the sine **still beeped**, so tick-stealing
was not the beep cause. Measuring balance vs block size settled it:

| block | balance | 480 / block |
|------:|--------:|------------:|
|   480 |  1.000  | 1.000 (perfect sine, 0 drops — host-confirmed by ear) |
|   512 |  0.940  | 0.9375 |
|  1024 |  0.470  | 0.469 |

`balance = 480 / block`. The SYSVAD WaveRT device advances a **fixed ~480-frame (10 ms @ 48k) notification
period per `KookrPullTick`**, but the user-mode master clock (the K11 output callback) fires one tick per
callback while consuming `block` frames. When `block != 480` the device and the output run at different
frame rates, the output FIFO starves (block > 480) or overruns (block < 480), and a continuous sine is
chopped into beeps whose pitch tracks the mismatch ratio — which is why 512 and 1024 beeped with different
tones. block = 480 is frame-exact and plays a clean sine.

## Why not just fix it in the driver (the counting semaphore)

The obvious fix is to make one tick carry a count: replace the auto-reset event with a **counting
semaphore**, release `floor(framesConsumed / period)` per callback (carry the remainder), and have the
pull thread drain the count one period-advance each. An auto-reset event coalesces (several `SetEvent`s
before the thread wakes collapse into one advance), so only a counting primitive can advance several
periods per callback.

It compiles and links, but it **does not load on this host**: Windows 10 19045's `ntoskrnl.exe` exports
**no semaphore create/open routine** for drivers — `ZwCreateSemaphore`, `ZwOpenSemaphore`,
`NtCreateSemaphore`, `NtOpenSemaphore` are all absent (verified with `dumpbin /exports`). The driver binds
but fails with `CM_PROB_DRIVER_FAILED_LOAD` (System log event 219). `ExSemaphoreObjectType`,
`IoCreateSynchronizationEvent`, and the section APIs (`ZwOpenSection` / `ZwMapViewOfSection`) *are*
exported, so a kernel fix is still possible two ways — a user-created semaphore referenced via an IOCTL
(`ObReferenceObjectByHandle`), or a shared-memory counter the driver maps and reads on each event wake —
but both add kernel surface (BSOD risk) for a result user space can achieve.

## Approach A (chosen): keep the event driver, fix the mismatch in user space

The device already plays clean at its own 480-frame period. So the consumer runs the **SYSVAD-coupled
output stage at exactly one period** (`blocksize = 480`, one `SetEvent` per callback = one period, no
coalescing) and lets the renderer keep its larger `--block` behind the existing output FIFO / cushion.
No driver change, no kernel risk, and it generalises: the renderer block is independent of the device
period.

Applied to the cockpit (`apps/voicemeeter_passthrough.py`) as an **opt-in** `--pull-sysvad` mode
(VoiceMeeter path stays the default and untouched):

- captures the SYSVAD 7.1 loopback (defaults `--input-device` to the Tablet endpoint, prefers the 8-ch one);
- creates `Global\KookrPullTick` (auto-reset event, NULL DACL) and fires one `SetEvent` from the K11
  output callback, with the output pinned to one period;
- the headphone clock becomes the **single master clock** driving SYSVAD — no two-clock drift, so the
  async resampler the VoiceMeeter path needs is unnecessary here;
- teardown closes the handle and the streams; it **never** unloads/disables/`devcon`-restarts the driver
  (journal 70: that bugchecks with a stream open).

## Operational notes learned this round

- The `\\wsl.localhost` 9P share is per-interactive-session: it is **not** visible to the OpenSSH service
  session, so host automation must stage files on the host's local disk, not run WSL-checkout code over UNC.
- `msbuild /t:Rebuild` of `EndpointsCommon` + `TabletAudioSample` via the EWDK (`SetupBuildEnv.cmd`) takes
  a few minutes; verify the built `.sys` with `dumpbin /imports` (expect the APIs you added) before signing.
- Clearing a leaked pull object needs only killing the user-mode process that holds the stream (the normal
  close path) — not a reboot and never a driver unload.
