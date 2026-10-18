"""GPU world-locked 7.1 render (CuPy) — real-time full-BRIR passthrough (B4.2).

An **optional** GPU path for the M4 passthrough. Pure-Python convolution of 8 channels of a
500 ms BRIR is ~0.97× RT (too slow), so this was once required; it no longer is — the native
Rust kernel (:mod:`binaural_headtracker.rust_render`, B8.2) renders it on the CPU at ~5× RT,
and the GPU here is just an alternative (~6× RT at block 256, full room tail). Prefer `--rust`;
`--gpu` remains for hosts that want it.

It uses the bus reformulation (:class:`binaural_headtracker.live_render.BusMixer`): the
world-lock is a time-varying crossfade of the 8 inputs into 8 **fixed-BRIR buses**
(mathematically identical to the per-channel :class:`~binaural_headtracker.live_render.
WorldLockedSource` voice pool — proven on CI), so the convolution becomes a *batched
fixed-filter* partitioned overlap-save that the GPU runs in one shot. The routing/crossfade
(``BusMixer``) is tested numpy; only the batched convolution runs on the GPU here, so this
module imports CuPy lazily and is host-only (no GPU on CI). The algorithm mirrors the
CPU :class:`~binaural_headtracker.convolve.PartitionedConvolver` (verified == ``fftconvolve``),
batched over (bus, ear).

See AGENTS.md "Running the host GUI apps" for the CuPy/CUDA-12 host setup and the
``\\wsl.localhost`` cwd gotcha.
"""

from __future__ import annotations

import numpy as np

from binaural_headtracker.live_render import BusMixer, bus_targets
from binaural_headtracker.surround import BakedBRIRSet


class GpuBusConvolver:
    """Batched partitioned overlap-save convolver on the GPU: ``C`` buses × 2 ears.

    Fixed filters (the ``C`` BRIRs). ``process(bus_in)`` takes a ``(C, block)`` host array
    (the bus signals) and returns ``(block, 2)`` stereo (summed over buses), keeping the
    frequency-domain delay line resident on the GPU; only the tiny input/output blocks
    cross the bus. ``dtype`` is the on-GPU precision (complex64 is ~2× faster and audibly
    exact; complex128 matches the CPU reference to ~1e-9 for validation).
    """

    def __init__(self, brir: BakedBRIRSet, block_size: int, *, dtype="complex64") -> None:
        import cupy as cp

        self._cp = cp
        self._block = int(block_size)
        self._nfft = 2 * self._block
        c = brir.n_channels
        p = max(1, -(-brir.n_taps // self._block))  # ceil
        self._p = p
        cdt = cp.complex64 if str(dtype) == "complex64" else cp.complex128
        self._fdt = cp.float32 if cdt == cp.complex64 else cp.float64
        # Precompute Hf (C, 2, P, F): each ear's IR partitioned into P blocks, rfft(2B).
        hf = np.zeros((c, 2, p, self._block), dtype=np.float64)
        for e, bank in enumerate((brir.left, brir.right)):
            padded = np.zeros((c, p * self._block))
            padded[:, : brir.n_taps] = bank
            hf[:, e] = padded.reshape(c, p, self._block)
        self._hf = cp.fft.rfft(cp.asarray(hf), n=self._nfft, axis=3).astype(cdt)  # (C,2,P,F)
        f = self._block + 1
        self._fdl = cp.zeros((c, p, f), dtype=cdt)   # input spectra delay line
        self._prev = cp.zeros((c, self._block), dtype=self._fdt)
        self._pos = 0
        self._c = c

    @property
    def block_size(self) -> int:
        return self._block

    def process(self, bus_in) -> np.ndarray:
        """Convolve one ``(C, block)`` bus block → ``(block, 2)`` stereo (host array)."""
        cp = self._cp
        x = cp.asarray(np.asarray(bus_in, dtype=np.float64), dtype=self._fdt)  # (C, block)
        if x.shape != (self._c, self._block):
            raise ValueError(f"bus_in must be ({self._c}, {self._block}); got {tuple(x.shape)}")
        window = cp.concatenate([self._prev, x], axis=1)  # (C, 2B)
        self._prev = x
        self._fdl[:, self._pos, :] = cp.fft.rfft(window, axis=1).astype(self._fdl.dtype)
        idx = (self._pos - cp.arange(self._p)) % self._p
        # Y[c,e,f] = Σ_p Hf[c,e,p,f] * Xsel[c,p,f]
        y = (self._hf * self._fdl[:, idx, :][:, None, :, :]).sum(axis=2)  # (C, 2, F)
        self._pos = (self._pos + 1) % self._p
        out = cp.fft.irfft(y, n=self._nfft, axis=2)[:, :, self._block :]  # (C, 2, block)
        ears = out.sum(axis=0)  # (2, block), contiguous — download then transpose on host
        return np.ascontiguousarray(cp.asnumpy(ears).T)  # (block, 2)


class GpuWorldLocked7p1:
    """GPU world-locked 7.1 → stereo: ``BusMixer`` (CPU) + :class:`GpuBusConvolver` (GPU).

    Drop-in for :class:`binaural_headtracker.live_render.WorldLocked7p1`:
    ``process_block(pcm (block, C), azimuths (C,)) -> (block, 2)``. Instantiating it builds
    the GPU filters, so it needs a working CuPy; the passthrough app selects it with
    ``--gpu`` and falls back to the CPU class otherwise.
    """

    def __init__(self, brir: BakedBRIRSet, *, block_size: int = 256, fade_ms: float = 20.0,
                 dtype="complex64", lfe_mode: str = "brir") -> None:
        if lfe_mode not in ("brir", "flat"):
            raise ValueError("lfe_mode must be 'brir' or 'flat'")
        self._brir = brir
        self._block = int(block_size)
        self._lfe_mode = lfe_mode
        fade_samples = max(1, round(fade_ms * 1e-3 * brir.samplerate))
        self._mixer = BusMixer(brir.n_channels, fade_samples=fade_samples)
        self._conv = GpuBusConvolver(brir, block_size, dtype=dtype)
        # Flat LFE (B4.3): keep the LFE out of the GPU buses (zero its input column) and sum it
        # flat on the host at the per-ear LFE-IR L2 norm — mirrors WorldLocked7p1's flat mode,
        # preserving sub-bass the bass-light BRIR would strip. See live_render.WorldLocked7p1.
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
        if self._lfe_mode == "flat" and self._lfe_idx is not None:
            pcm = pcm.copy()
            lfe = pcm[:, self._lfe_idx].copy()
            pcm[:, self._lfe_idx] = 0.0  # keep LFE out of the convolved buses
        bus_in = self._mixer.mix(pcm, bus_targets(self._brir, azimuths))  # (C, block)
        out = self._conv.process(bus_in)
        if self._lfe_mode == "flat" and self._lfe_idx is not None:
            out[:, 0] += lfe * self._lfe_gain[0]
            out[:, 1] += lfe * self._lfe_gain[1]
        return out
