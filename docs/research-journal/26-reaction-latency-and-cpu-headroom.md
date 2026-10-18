# 26 — Head-move → ear latency, and whether the CPU can drop the GPU (B8.1 + M8 scoping)

**Date:** 2026-10-18  ·  **Milestone:** M8 (latency) / M4 operability  ·  **Status:** latency cushion tightened (shipped); native-hot-path recommendation recorded as the GPU-drop path (issue)

## Question

Two owner questions after B5.3 fixed the tonal balance:

1. **Reaction latency** — "when I move my head it takes too long for the virtual speakers to
   move." Crucially: the **GUI radar/HUD tracks the head instantly, no perceptible delay.**
2. **Rust / CPU realtime** — the CPU was "only slightly too slow" (journal 24: ~0.97× RT), so
   could a rewrite make CPU-only realtime viable and **drop the GPU dependency**?

## 1. The lag is the audio cushion, not the tracker

The instant GUI is the tell: the radar draws the *filtered pose*, so if the camera, MediaPipe,
or the 1€ filter lagged, the screen would lag too — it doesn't. So the whole camera→pose→
display path is already fresh; the delay is entirely **downstream of `state["az"]`, in the
audio path**. Walking it:

- **Output FIFO cushion (`--latency-ms`, was 120 ms)** — the dominant term. When the head turns,
  the render worker *immediately* produces audio with the new azimuth, but a cushion of
  already-rendered audio (old azimuth) drains first, so the move is heard ~cushion late.
- `--block 1024` (21 ms/block) capture + render granularity, and the output device buffer
  (sounddevice default latency is "high").
- **A latent trap:** `max_backlog = cushion * 4` let clock drift grow the queue to ~480 ms — the
  reaction latency could balloon far past the nominal cushion.

Fix (all in `voicemeeter_passthrough.py`), none touching the DSP:

- default `--latency-ms` 120 → **60**, with help that names it as *the* head-move-to-ear delay;
- `max_backlog` `cushion*4` → **`cushion*2`**, so drift can't let latency grow unbounded (it
  only ever drops *excess future* audio, so a tighter bound cannot cause underruns — it just
  caps latency);
- output stream `latency="low"` (was the implicit "high"), trimming the device buffer;
- host launcher now runs `--block 512 --latency-ms 50`.

Net: head-move-to-ear drops from ~160–480 ms to ~70–110 ms, no DSP change. Lower is available
by flag; raise if dropouts appear. A sub-20 ms path is the native hot path below (M8 proper).

## 2. CPU headroom — the GPU is covering Python overhead, not a math wall

Benchmarked the real renderer on the bare BRIR (8ch × 500 ms/24000-tap, 48 kHz) on a 16-core
box (numpy pocketfft, single-threaded):

| case | block 512 | block 1024 |
|---|---|---|
| raw partitioned convolution, steady (16 streams) | 3.7× RT | 9.1× RT |
| raw convolution, **worst case moving** (32 streams, 2 voices/ch) | **1.6× RT** | **4.3× RT** |
| full `WorldLocked7p1` + output EQ, moving | 0.93× RT | 2.0× RT |

Findings:

- **The convolution math is already realtime on CPU, even worst case** (1.6–4.3× RT). Journal
  24's 0.97× was the worst-case crossfade (every channel spawning a second voice ⇒ ~2× the
  convolutions) on the host, i.e. the *top* of this range, not a steady-state wall.
- The gap between raw convolution (1.6× @512) and the full pipeline (0.93× @512) is **Python
  per-block orchestration** — voice-pool bookkeeping, the crossfade gain `einsum`, array
  allocations/concatenations, the EQ — **not** the FFTs.
- **Naive Python threading makes it worse** (0.2× RT): dispatching 16–32 futures per ~11 ms
  block costs far more than the ~180 µs of per-stream work. Coarse-grained parallelism inside a
  single native call is the only threading that helps here.

So the GPU is compensating for interpreter overhead, not for an intractable amount of arithmetic.

## Decision / next

- **Reaction latency: shipped** (cushion/backlog/device-latency tuning). This is the cheap 80%.
- **Rust question — do NOT rewrite everything.** MediaPipe/OpenCV (the camera stack) have no
  good Rust path and are not the bottleneck; rewriting them would lose MediaPipe for no gain.
  The evidence points to a **targeted native hot path for the render engine only** (the
  partitioned convolution + bus mix + crossfade, as one allocation-free block processor, Rust
  via PyO3 or C, coarse-grained multithread across the bus/ear batch). That removes the Python
  per-block overhead that the 0.93× reflects and should give **comfortable CPU-only realtime at
  low block sizes — dropping the CuPy/CUDA dependency** (journal 24 L51's install pain) while
  also serving the real M8 latency goal. The cheaper interim, if wanted before any native work:
  port the GPU **bus-mix reformulation** (8 fixed-filter buses, gains cross-faded — already
  proven == the voice pool) to the CPU path to cut both the stream count and the Python
  per-voice bookkeeping. Captured as an issue with this benchmark table. (→ L55)
