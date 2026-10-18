"""Native CPU world-locked 7.1 render (Rust/PyO3) — real-time without a GPU (B8.2).

The CPU *can* convolve 8 channels of a 500 ms BRIR in real time — the arithmetic is several×
real time on one core (journal 26); what fell short was Python per-block overhead, not the
FFTs. So this is the GPU-free render path: the same **bus reformulation** the GPU uses
(:class:`binaural_headtracker.live_render.BusMixer` — the world-lock as a crossfade of the 8
inputs into 8 fixed-BRIR buses, proven identical to the per-channel voice pool), with the one
heavy piece — the batched partitioned overlap-save convolution of the buses — done in a native
Rust kernel (``binaural_kernel.BusConvolver``, rustfft + rayon, allocation-free per block,
multithreaded across the bus×ear batch) instead of on the GPU.

Drop-in for :class:`binaural_headtracker.live_render.WorldLocked7p1` and
:class:`binaural_headtracker.gpu_render.GpuWorldLocked7p1`:
``process_block(pcm (block, C), azimuths (C,)) -> (block, 2)``. The routing/crossfade and LFE
handling stay in tested Python (identical to the GPU wrapper); only the convolution is native,
and it mirrors the Python ``PartitionedConvolver`` (verified ``== fftconvolve``) bin for bin,
so this matches ``WorldLocked7p1`` to floating error.

Needs the native kernel built into the venv (``maturin develop --release -m rust/Cargo.toml``);
the passthrough app selects it with ``--rust`` and falls back to CPU/GPU otherwise.
"""

from __future__ import annotations

import numpy as np

from binaural_headtracker.live_render import BusMixer, bus_targets
from binaural_headtracker.surround import BakedBRIRSet


class RustWorldLocked7p1:
    """Rust-kernel world-locked 7.1 → stereo: ``BusMixer`` (CPU) + native batched convolver."""

    def __init__(self, brir: BakedBRIRSet, *, block_size: int = 256, fade_ms: float = 20.0,
                 lfe_mode: str = "brir") -> None:
        if lfe_mode not in ("brir", "flat"):
            raise ValueError("lfe_mode must be 'brir' or 'flat'")
        import binaural_kernel  # built separately (optional native accelerator)

        self._brir = brir
        self._block = int(block_size)
        self._lfe_mode = lfe_mode
        fade_samples = max(1, round(fade_ms * 1e-3 * brir.samplerate))
        self._mixer = BusMixer(brir.n_channels, fade_samples=fade_samples)
        self._conv = binaural_kernel.BusConvolver(
            np.ascontiguousarray(brir.left, dtype=np.float64),
            np.ascontiguousarray(brir.right, dtype=np.float64),
            self._block,
        )
        # Flat LFE (B4.3): keep LFE out of the convolved buses and sum it flat on the host at the
        # per-ear LFE-IR L2 norm — identical to WorldLocked7p1 / GpuWorldLocked7p1.
        self._lfe_idx = brir.channels.index("LFE") if "LFE" in brir.channels else None
        self._lfe_gain = (0.0, 0.0)
        if lfe_mode == "flat" and self._lfe_idx is not None:
            li = self._lfe_idx
            self._lfe_gain = (float(np.linalg.norm(brir.left[li])),
                              float(np.linalg.norm(brir.right[li])))

    @property
    def block_size(self) -> int:
        return self._block

    @property
    def samplerate(self) -> int:
        return self._brir.samplerate

    def process_block(self, pcm, azimuths) -> np.ndarray:
        pcm = np.asarray(pcm, dtype=np.float64)
        c = self._brir.n_channels
        if pcm.ndim != 2 or pcm.shape != (self._block, c):
            raise ValueError(f"pcm must be ({self._block}, {c}); got {pcm.shape}")
        lfe = None
        if self._lfe_mode == "flat" and self._lfe_idx is not None:
            pcm = pcm.copy()
            lfe = pcm[:, self._lfe_idx].copy()
            pcm[:, self._lfe_idx] = 0.0  # keep LFE out of the convolved buses
        bus_in = self._mixer.mix(pcm, bus_targets(self._brir, azimuths))  # (C, block)
        out = self._conv.process(np.ascontiguousarray(bus_in))
        if lfe is not None:
            out[:, 0] += lfe * self._lfe_gain[0]
            out[:, 1] += lfe * self._lfe_gain[1]
        return out
