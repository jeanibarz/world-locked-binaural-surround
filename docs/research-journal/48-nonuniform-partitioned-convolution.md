# 48 — Non-uniform partitioned convolution: bit-exact, but the kernel isn't MAC-bound (B8.8)

**Date:** 2026-10-18  ·  **Milestone:** M8 (native hot path)  ·  **Status:** implemented and **bit-exact** (2.3e-14 vs scipy / the Python uniform engine), but **no end-to-end speedup at the real BRIR size** — the premise (MAC-bound) does not hold; **not merged**, kept on the branch for owner review

## Question

The native render kernel (journal 27) is a uniformly-partitioned overlap-save (UPOLS) convolver:
for the ~24000-tap BRIR at block 512 that is `P ≈ 47` equal partitions, i.e. 47 complex
multiply-accumulate (MAC) passes per block. Issue #52 reasoned this is **MAC-bound** and that
**non-uniform** partitioned convolution (NUPC — small head partitions, larger tail partitions that
update far less often; Gardner 1995, García 2002) should cut the per-block MAC cost ~2–3× with
**zero sound change** (output stays `== scipy.fftconvolve` to floating error). This block tests
that claim.

## Change

Replaced the uniform partitioning in `rust/src/lib.rs` (`binaural_kernel.BusConvolver`) with NUPC,
keeping the Python API (`new(left, right, block)` / `process(bus_in)`) unchanged, realfft-based,
rayon-batched across the buses, and allocation-free per block.

- **Exact decomposition.** Split the filter `h` (length `N`) into contiguous segments; segment `i`
  occupies taps `[a_i, a_i+len_i)`. By linearity
  `y = Σ_i delay(a_i, conv(h[a_i:a_i+len_i], x))`: each segment is an independent UPOLS convolver
  run at its own partition length `L_i` (a multiple of the block `B`), its output delayed by `a_i`.
  This equals the full linear convolution **regardless of the per-segment FFT sizes**, so the output
  is bit-identical to the old kernel / `scipy.fftconvolve` to round-off.
- **Schedule.** A head stage at block size `B`, then tail stages whose partition length doubles
  (`B, 2B, 4B, …`), `group` partitions each. A stage run at length `L` collects `L/B` blocks before
  transforming, so it is causal/in-time for the `B`-block output iff `a_i >= L_i − B` — which the
  schedule guarantees (the head covers enough taps before the first larger stage starts). Each
  super-block's output is accumulated, delayed, into a per-ear **output ring**; each block reads its
  `B` samples and zeroes them. An optional `schedule` arg overrides the built-in policy (for the
  benchmarks); default `group=4`.
- **Test.** `test_nupc_kernel_matches_reference` asserts the native kernel equals **both**
  `scipy.fftconvolve` and the Python `PartitionedConvolver` to `< 1e-11` across blocks
  128/256/512/1024 and filters up to 24000 taps.

## Method

A numpy prototype first proved the decomposition exact and the `a_i >= L_i − B` schedulability
constraint. The Rust port was then verified against `scipy.fftconvolve` and the shipped
`PartitionedConvolver`, and benchmarked on a real-shaped **8×24000-tap** BRIR at blocks 256/512/1024
(median **and max** per-block time, to watch load predictability), plus two probes: a fixed-overhead
floor (a 1-tap filter), and a sweep of filter length `N` and bus count to find where NUPC's MAC
saving actually pays off. 16-core WSL2 box.

## Result

**Bit-exactness: excellent.** Worst error across every block size, filter length, and schedule, vs
both references, was **2.31e-14** — far under the 1e-12 target. The 7 existing kernel tests and 12
new ones pass; full suite 356 passed; ruff clean. The algorithm is correct.

**Speed at the real size: no win.** Median per-block time, 8×24000-tap BRIR:

| block | uniform | NUPC (best) | speedup | NUPC max/block | uniform max/block |
|---|---|---|---|---|---|
| 256 | 0.81 ms | 0.77 ms | **1.06×** | 1.46–3.16 ms | 1.17 ms |
| 512 | 0.83 ms | 0.80 ms | **1.03×** | 1.43–2.10 ms | 1.14 ms |
| 1024 | 0.90 ms | 0.89 ms | **1.00×** | 1.23–2.26 ms | 1.36 ms |

**The kernel is not MAC-bound — it is FFT + per-call-overhead bound.** A 1-tap filter (so ~no MAC,
one fwd + two inv FFTs per bus) already costs **0.69 / 0.73 / 0.90 ms** at block 256/512/1024 — i.e.
~85–90 % of the full 8×24000 cost is independent of how the filter is partitioned (the FFTs, the
Python→Rust FFI, the per-block output-array allocation, the rayon fork/join over 8 buses). NUPC cuts
the ~12 % that is MAC (94→11 partitions at block 256) but **adds** FFT work (more, larger
transforms), so the two roughly cancel.

**NUPC's saving is real — it just needs a regime where compute dominates the fixed overhead.** Same
kernel, block 512, 8 buses: `N=48000 → 1.26×`, `N=96000 → 2.36×` (2.00 → 0.85 ms). At **32 buses ×
96000 taps** it is **5.0×** (9.74 → 1.94 ms). So the partitioning win appears once the MAC passes
outweigh the per-call FFT/FFI floor — not at the current 8×24000.

**Load predictability regresses.** NUPC's big tail FFTs fire in correlated bursts across all buses,
so the **max** per-block time is 1.3–3× the uniform kernel's (table above). This violates issue
#52's "per-block cost still constant". A larger `group` (fewer distinct FFT sizes) softens the burst
but does not remove it; truly flat load needs Gardner's distributed sub-block scheduling — much more
complexity for a size where there is no speed payoff anyway.

## Interpretation

Issue #52's core assumption — "the per-block cost is dominated by the 47 MAC passes, not the FFTs" —
**is wrong for this realfft implementation at this size on this hardware.** A modern CPU vectorises
47 complex MACs over ~513 bins into roughly nothing next to three `2B`-point real FFTs per bus and
the fixed per-call overhead. Journal 26 already found the convolution math is comfortably real-time
and the old headroom was *Python overhead*; this result is the same lesson one level down — at
8×24000 the remaining per-block cost is FFT + FFI/allocation overhead, which NUPC does not touch (and
mildly worsens). The decomposition and the implementation are sound; the *workload* simply isn't in
the regime where NUPC helps.

## Decision / next

- **Do not merge; leave the PR open for owner review.** Acceptance ("benchmarked faster on the real
  8×24000 BRIR" and "no load-predictability change") is not met: ~1.0× and burstier. Shipping a
  more-complex kernel that is no faster and less predictable at the real size would be a net
  regression. The uniform kernel (journal 27) stays the default.
- **The real lever at this size is per-call overhead, not the convolution algorithm** — the journal
  27 "next" items: an **in-place output buffer** (drop the per-block stereo allocation) and
  **batching the FFI** (one call does mix + convolve + LFE). That attacks the 85–90 % that actually
  dominates. NUPC only becomes worth it for much longer filters (≥ ~48k taps) or many more buses.
- The NUPC implementation is bit-exact and kept on the branch as a validated foundation should a
  future BRIR/room-reverb be long enough to need it. (→ L79)
