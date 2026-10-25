# 82 — MMCSS boosts one thread; a background compile starved the whole engine

**Block:** B8.7c follow-up (web cockpit, feat/cockpit-ui-redesign) · **Milestone:** M8 / Track B ·
**Status:** mechanism reproduced live on the host (a background `cargo build` made the DSP
inaudible); whole-process HIGH-priority elevation applied, pending the owner's confirm that normal
listening is unaffected and that heavy background load no longer drops audio.

## Question

While building the Tauri installer on the host (`cargo tauri build`, all cores busy), the owner's
running engine went **inaudible with a flood of dropouts**. The render worker is already registered
for MMCSS "Pro Audio" (`_elevate_to_pro_audio`, journal-era B0.x), which is what DAWs use — so why
does a plain, *normal*-priority background compiler starve it?

## Context

`_elevate_to_pro_audio()` calls `AvSetMmThreadCharacteristicsW("Pro Audio")` on **the current
thread** — the render worker — and bumps its MMCSS priority. That boosts exactly one thread. But the
worker is not self-sufficient: the convolution runs GIL-free (`rust/src/lib.rs` `py.allow_threads`),
yet every block the worker also needs the **GIL** for its Python glue, and the GIL is held by the
camera-capture / MediaPipe-detect / main-loop threads. Those threads run at the **process base
priority class**, which is `NORMAL` — the same band as a `cargo`/`rustc` worker.

## Hypothesis

MMCSS protects the worker's *scheduling slot*, but not the GIL it depends on. When a normal-priority
compiler saturates every core, the engine's normal-priority GIL holders get no CPU, so they can't
release the GIL promptly; the MMCSS-boosted worker then blocks on the GIL and misses its block
deadline anyway. The boost is real but it guards the wrong resource — a single boosted thread in a
process full of normal-priority threads it must cooperate with is only as fast as its slowest
GIL partner under contention.

## Evidence

- Live: dropping the compiler's process tree to **Idle** priority (so it yields to anything) made
  the dropouts stop instantly while the build kept running — i.e. the fix was about *relative*
  priority between the engine's normal threads and the competitor, not raw compute. The engine's
  render load was a healthy ~55–65% throughout; this was a scheduling-contention stall, not a
  throughput shortfall (the mirror of journal 76's GIL-hand-off finding).
- Code: `_elevate_to_pro_audio` elevates one thread; nothing sets the process **priority class**, so
  the camera/detect/main-loop threads — the worker's GIL partners — sit at `NORMAL`.

## Fix

Raise the **whole process** to `HIGH_PRIORITY_CLASS` once at startup (`_raise_process_priority`,
next to the existing `gc.freeze()` + `sys.setswitchinterval(0.0005)` real-time tuning). That lifts
*all* the engine's threads above normal-priority background work, so a compiler/indexer/update task
can no longer starve the GIL holders out from under the MMCSS-boosted worker. `HIGH` is the pro-audio
norm; `REALTIME` is deliberately avoided — it outranks kernel/driver threads and can stall audio I/O
and the whole box. Best-effort and Windows-only; the class reverts when the process exits.

A ctypes trap fell out of writing it: `GetCurrentProcess()` returns a 64-bit HANDLE, and without
`restype = c_void_p` ctypes truncates the pseudo-handle, so `SetPriorityClass` fails **silently**
and the process quietly stays at normal priority (verified: `ok=0` until the signatures were set,
`ok=1` → class `0x80` after). Signatures are now set explicitly.

## Learnings

- MMCSS boosts a **thread**, not a process. In a multi-thread process where the boosted thread
  depends on a shared lock (here the GIL) held by *un*-boosted threads, the boost is defeated by
  contention on that lock. Lift the whole process, or boost every thread on the critical path.
- A real-time symptom under external load is usually *relative* priority, not absolute compute:
  the engine had 35–45% idle headroom and still dropped out. Deprioritize the competitor **or**
  elevate the engine; raw core count was never the bottleneck.
- Operationally: don't run heavy builds on the host while listening. `host/build-tauri.sh`-class
  work belongs at Idle priority (or off-session) even with the process elevation in place.
- ctypes + Win32 HANDLEs: always set `restype`/`argtypes`. A truncated handle fails silently and
  looks like "the API didn't do anything," not an error.

## Decision / next

- **Applied:** `_raise_process_priority()` → process `HIGH` at startup, logged as `process
  priority: HIGH`; explicit ctypes signatures.
- **Owner confirm (the gate):** normal listening sounds unchanged, and a deliberate background load
  (e.g. a compile at normal priority) no longer drives dropouts the way it did before. Audio is
  judged on the host, so this is not self-merged.
- If HIGH proves too aggressive on the desktop (UI/other apps feel starved), step down to
  `ABOVE_NORMAL_PRIORITY_CLASS` — same mechanism, one constant.
