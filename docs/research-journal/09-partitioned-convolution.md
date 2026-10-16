# 09 — Partitioned-FFT convolution engine (B1.3)

**Date:** 2026-10-16  ·  **Milestone:** M1  ·  **Status:** done

## Question

Can we convolve each speaker channel with its (B)RIR **in real time** — block by
block, as audio streams from the virtual device — at a per-block cost that fits
the latency budget, while producing *exactly* what an offline
`scipy.signal.fftconvolve` would?

## Context

B1.6 (journal 07) renders 7.1 → stereo by calling `scipy.signal.fftconvolve`
once over a whole PCM array — fine offline, useless live: it needs the entire
signal up front and its FFT grows with the signal length. The live engine (FR-5,
M4) consumes a continuous stream in small blocks and must emit a matching output
block each time, at bounded, constant cost. `surround.render_7p1_to_stereo`'s
docstring already named this missing piece "the real-time partitioned engine is
B1.3".

The latency budget (requirements § 7) gives the **convolution block** ~5–10 ms
in the MVP (~1–5 ms native). At 48 kHz that is a block of a few hundred samples —
and the block size *is* the input-buffering latency, because you must collect a
whole block before you can process it.

## Hypothesis

**Uniformly-partitioned overlap-save (UPOLS)** decouples the block size from the
filter length and gives constant per-block work:

- Pick block size `B` from the latency budget. FFT size `= 2B`.
- Chop the filter (`N` taps) into `P = ceil(N / B)` partitions of `B` taps.
  Pre-transform each to the `2B`-point frequency domain → `H_p`.
- Keep a frequency-domain delay line (FDL) of the last `P` input-window spectra
  (each window is the last `2B` input samples, hop `B`). Per block: one `2B`
  forward FFT, `P` complex multiply-accumulates `Y = Σ_p H_p · X[n-p]`, one `2B`
  inverse FFT; the alias-free output is the **second half** of the IFFT.

Predictions: (1) streaming output reproduces `fftconvolve(x, ir)` to floating
error; (2) **no algorithmic delay beyond the one block** of buffering — output
block `m` carries the convolution of input block `m`; (3) per-block cost is
constant in the filter length for fixed `B` (it is `O(P)` MACs + two fixed FFTs),
comfortably real-time in Python for realistic BRIRs.

## Method

- New module `binaural_headtracker.convolve` (pure numpy, headless):
  - `PartitionedConvolver(ir, block_size)` — single-channel UPOLS stream.
    `process_block(B samples) → B samples`; `flush()` emits the filter tail
    (`tail_blocks` zero blocks); `reset()` clears state. Exposes `latency`,
    `n_parts`, `tail_blocks`.
  - `StereoConvolver(left, right, block_size)` — `C`-in → stereo-out, the live
    counterpart of `render_7p1_to_stereo` (per-channel L/R convolve + per-ear
    sum). Kept generic (takes IR banks, not a `BakedBRIRSet`).
  - `convolve_stream(x, ir, block)` — offline helper that streams a whole signal
    and appends the tail; equals `fftconvolve(x, ir)`.
- 38 tests (`tests/test_convolve.py`): streaming-vs-`fftconvolve` across a grid
  of block sizes `{1,4,16,64,100}` × IR lengths `{1,5,16,33,200}` (both `P=1`
  and `P>1`); the no-extra-delay block-alignment test; impulse recovery;
  streaming linearity/scaling; `reset` determinism; `flush=False` real-time
  region; framing/introspection (`latency`, `n_parts`, `tail_blocks`); stereo
  equivalence to `render_7p1_to_stereo`; error handling.
- Benchmark (`scratchpad`, not committed): per-block wall-clock vs the block's
  own wall-clock budget, single-channel and 8-channel 7.1→stereo, at 48 kHz.

One variable at a time: correctness is pinned against the already-trusted
`fftconvolve` (B1.6's engine); performance is read separately from the benchmark.

## Result

**Correctness.** Exact to floating error. Max abs error vs
`scipy.signal.fftconvolve` for a 4000-sample signal × 600-tap IR at block 256:
**5.68e-14**. All 38 new tests pass; full suite **110 passed**; ruff clean.

**Latency framing.** `latency == block_size` (one block of buffering); no extra
algorithmic delay — the block-alignment test confirms output block `m` equals
`fftconvolve(x, ir)` over input block `m`'s samples.

**Per-block cost** (48 kHz; `xRT` = block wall-clock budget ÷ measured time;
≥ 1 is real-time):

| IR len | block | parts | ms/block | budget ms | ×RT |
|---|---|---|---|---|---|
| 50 ms | 256 | 10 | 0.052 | 5.33 | 102 |
| 200 ms | 128 | 75 | 0.150 | 2.67 | 18 |
| 200 ms | 256 | 38 | 0.098 | 5.33 | 55 |
| 500 ms | 256 | 94 | 0.194 | 5.33 | 28 |

**8-channel 7.1 → stereo**, 200 ms BRIRs (16 convolvers, the realistic live
load):

| block | parts/ch | ms/block | budget ms | ×RT |
|---|---|---|---|---|
| 128 | 75 | 2.61 | 2.67 | **1.0** |
| 256 | 38 | 1.65 | 5.33 | 3.2 |
| 512 | 19 | 1.35 | 10.67 | 7.9 |

## Interpretation

The structure is right: output matches the offline engine to 1e-14, and the only
latency is the block itself — exactly the real-time convolution primitive M4
needs. Single-channel is real-time by a wide margin even for a 500 ms reverberant
BRIR.

The honest ceiling is the **full 8-channel stereo mix in pure Python**: at block
128 (2.67 ms) it is only **1.0×RT** — no headroom, because the per-block Python
loop over 16 convolvers × up to 75 partitions dominates (NumPy per-call overhead,
not FLOPs). Block **256 (5.33 ms)** restores 3.2×RT and sits squarely in the § 7
MVP convolution-block budget (5–10 ms), so it is the prototype's sensible default;
128 is viable only once the FDL MAC is vectorised across partitions/channels or
the hot path goes native. This is precisely the evidence M8 (B8.1) is meant to
act on — the prototype proves the perceptual loop; the native port buys the
sub-5 ms block.

Negative/limitation: the current `StereoConvolver.process_block` loops channels
in Python and each `PartitionedConvolver` loops its `P` partitions in Python.
Both are vectorisable (stack `H` and the FDL, do the MAC as one `einsum`/tensordot,
batch the FFTs across channels). Not done here — block 256 already clears the
budget and premature vectorisation would obscure the clean reference
implementation. Logged as a follow-up (B1.9).

## Learnings

- UPOLS makes block size a free latency knob independent of filter length; the
  per-block cost is `O(P)` MACs + two fixed-size FFTs — constant in `N` for fixed
  `B`. (→ L23)
- In a Python convolution prototype the bottleneck is per-call overhead
  (channels × partitions Python iterations), not arithmetic: block size trades
  latency for fewer, larger iterations, and vectorising the MAC/FFT across
  partitions and channels is the lever before going native. (→ L24)
- Overlap-save with FFT size `2B` and hop `B` yields the alias-free linear
  convolution in the **second half** of each IFFT, and the streamed result equals
  the offline `fftconvolve` with no extra delay — a cheap, exact correctness
  oracle for any future convolution refactor. (→ L25)

## Decision / next

B1.3 **done**: a correct, real-time, latency-bounded convolution engine with a
generic single-channel core and a 7.1→stereo wrapper matching B1.6's offline
renderer. Default live block size for the Python prototype: **256 @ 48 kHz
(5.3 ms)**.

Added follow-up **B1.9** (vectorise the FDL MAC and batch FFTs across
partitions/channels to reclaim block-128 headroom in pure Python) — do it only if
M4 needs sub-5 ms before the M8 native port. Next eligible M1 block is **B1.4**
(7.1 world-layout model → per-channel (az,el) for a fixed head), after which
**B1.5** wires B1.1–B1.4 into the generic-SOFA static renderer using this engine.
