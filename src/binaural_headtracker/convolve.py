"""Partitioned-FFT (overlap-save) streaming convolution engine (B1.3).

The render engine must convolve each speaker channel with a (B)RIR *in real
time*, block by block, as audio streams from the virtual device — not as one
offline ``scipy.signal.fftconvolve`` over a whole file (:mod:`surround`, B1.6).
This module is that streaming core.

Why *uniformly-partitioned* overlap-save (UPOLS)
------------------------------------------------
A plain overlap-save grows its FFT with the filter length, so a long BRIR forces
a large FFT and a large block — and the block size *is* the input-buffering
latency. UPOLS decouples the two: pick the block size ``B`` from the latency
budget (``docs/requirements.md`` § 7: the convolution block wants ~5–10 ms, i.e.
a few hundred samples at 48 kHz), then chop the filter into ``P = ceil(N / B)``
partitions of ``B`` taps each. Every block costs one ``2B`` forward FFT, ``P``
complex multiply-accumulates, and one ``2B`` inverse FFT — constant work per
block, independent of where in the filter a tap sits. This is the standard
real-time convolution structure (Stockham / Gardner / Wefers) and the thing M8
later ports to a native hot path.

Latency. The algorithm adds **no delay beyond the block**: the output block for
input samples ``[mB, (m+1)B)`` carries their fully-convolved result. The only
latency is the ``B`` samples you must collect before a block can be processed —
reported as :pyattr:`PartitionedConvolver.latency`.

Correctness. Streaming a signal through the convolver (then flushing the
filter tail) reproduces ``scipy.signal.fftconvolve(x, ir)`` exactly, to floating
error — this is what the B1.3 tests pin.

Everything here is pure numpy, so it runs and is unit-tested headless.
"""

from __future__ import annotations

import numpy as np

__all__ = [
    "PartitionedConvolver",
    "StereoConvolver",
    "convolve_stream",
]


def _as_filter(ir) -> np.ndarray:
    ir = np.asarray(ir, dtype=np.float64).ravel()
    if ir.size == 0:
        raise ValueError("impulse response must be non-empty")
    return ir


class PartitionedConvolver:
    """Single-channel streaming convolver (uniformly-partitioned overlap-save).

    Convolve a mono stream with one impulse response ``ir``, ``block_size``
    samples at a time. Call :meth:`process_block` with successive blocks of
    exactly ``block_size`` samples; each returns the matching ``block_size``
    output samples. The filter tail (the last ``len(ir) - 1`` samples of the
    full linear convolution) is flushed by feeding further zero blocks — see
    :meth:`flush` / :pyattr:`tail_blocks`.

    The instance holds state (the frequency-domain delay line), so it is a
    *stream* object: one per channel, not shared across streams. :meth:`reset`
    clears it.
    """

    def __init__(self, ir, block_size: int) -> None:
        if block_size < 1:
            raise ValueError("block_size must be >= 1")
        ir = _as_filter(ir)
        self._block = int(block_size)
        self._fft = 2 * self._block
        # Partition the filter into P parts of `block` taps (last zero-padded),
        # then pre-transform each part to the 2B-point frequency domain.
        n_parts = max(1, -(-ir.size // self._block))  # ceil division
        padded = np.zeros(n_parts * self._block)
        padded[: ir.size] = ir
        parts = padded.reshape(n_parts, self._block)
        # (P, B+1) half-spectrum of each zero-padded partition.
        self._H = np.fft.rfft(parts, n=self._fft, axis=1)
        self._n_parts = n_parts
        self._ir_len = ir.size
        self.reset()

    # -- introspection ---------------------------------------------------
    @property
    def block_size(self) -> int:
        return self._block

    @property
    def n_parts(self) -> int:
        return self._n_parts

    @property
    def ir_len(self) -> int:
        return self._ir_len

    @property
    def latency(self) -> int:
        """Input-buffering latency in samples (one block); no extra algorithmic delay."""
        return self._block

    @property
    def tail_blocks(self) -> int:
        """Zero blocks needed after the input to flush the full filter tail."""
        return -(-(self._ir_len - 1) // self._block) if self._ir_len > 1 else 0

    # -- state -----------------------------------------------------------
    def reset(self) -> None:
        """Clear the delay line and input history (as if freshly constructed)."""
        # Frequency-domain delay line: spectra of the last P input windows,
        # stored newest-at-`_pos`, indexed circularly.
        self._fdl = np.zeros((self._n_parts, self._block + 1), dtype=np.complex128)
        self._pos = 0
        self._prev = np.zeros(self._block)  # previous input block (overlap-save history)

    def process_block(self, x) -> np.ndarray:
        """Convolve one ``block_size``-sample block; return ``block_size`` samples."""
        x = np.asarray(x, dtype=np.float64).ravel()
        if x.size != self._block:
            raise ValueError(f"block must have {self._block} samples; got {x.size}")
        # Overlap-save window = [previous block, current block] (2B samples).
        window = np.concatenate((self._prev, x))
        self._prev = x.copy()
        # Store this window's spectrum as the newest FDL entry.
        self._fdl[self._pos] = np.fft.rfft(window)
        # Y = sum_p H[p] * X[n-p]: newest window multiplies partition 0.
        acc = np.zeros(self._block + 1, dtype=np.complex128)
        for p in range(self._n_parts):
            acc += self._H[p] * self._fdl[(self._pos - p) % self._n_parts]
        self._pos = (self._pos + 1) % self._n_parts
        # Overlap-save: the alias-free linear-convolution samples are the 2nd half.
        return np.fft.irfft(acc, n=self._fft)[self._block :]

    def flush(self) -> np.ndarray:
        """Feed ``tail_blocks`` zero blocks and return the concatenated tail."""
        if self.tail_blocks == 0:
            return np.zeros(0)
        zero = np.zeros(self._block)
        return np.concatenate([self.process_block(zero) for _ in range(self.tail_blocks)])


class StereoConvolver:
    """Multi-channel-in → stereo-out streaming convolver.

    The real-time counterpart of :func:`binaural_headtracker.surround.render_7p1_to_stereo`:
    each of ``C`` input channels is convolved with its own left-ear and
    right-ear IR, and the per-ear results are summed to a stereo block — exactly
    an EqualizerAPO baked binaural-surround config, but block-by-block.

    ``left`` and ``right`` are ``(C, N)`` IR banks (one row per channel), e.g.
    ``brir.left`` / ``brir.right`` from a
    :class:`~binaural_headtracker.surround.BakedBRIRSet`. Construction is kept
    free of the BRIR type so this stays a generic engine.
    """

    def __init__(self, left, right, block_size: int) -> None:
        left = np.asarray(left, dtype=np.float64)
        right = np.asarray(right, dtype=np.float64)
        if left.ndim != 2 or right.ndim != 2:
            raise ValueError("left/right must be 2-D (C, N)")
        if left.shape[0] != right.shape[0]:
            raise ValueError("left/right must have the same channel count")
        self._n_channels = int(left.shape[0])
        if self._n_channels == 0:
            raise ValueError("need at least one channel")
        self._left = [PartitionedConvolver(left[c], block_size) for c in range(self._n_channels)]
        self._right = [PartitionedConvolver(right[c], block_size) for c in range(self._n_channels)]
        self._block = int(block_size)

    @property
    def block_size(self) -> int:
        return self._block

    @property
    def n_channels(self) -> int:
        return self._n_channels

    @property
    def latency(self) -> int:
        return self._block

    @property
    def tail_blocks(self) -> int:
        return self._left[0].tail_blocks

    def reset(self) -> None:
        for conv in (*self._left, *self._right):
            conv.reset()

    def process_block(self, pcm) -> np.ndarray:
        """Convolve one ``(block_size, C)`` block → ``(block_size, 2)`` stereo."""
        pcm = np.asarray(pcm, dtype=np.float64)
        if pcm.ndim != 2 or pcm.shape != (self._block, self._n_channels):
            raise ValueError(
                f"pcm must be ({self._block}, {self._n_channels}); got {pcm.shape}"
            )
        out = np.zeros((self._block, 2))
        for c in range(self._n_channels):
            x = pcm[:, c]
            out[:, 0] += self._left[c].process_block(x)
            out[:, 1] += self._right[c].process_block(x)
        return out

    def flush(self) -> np.ndarray:
        """Flush every channel's filter tail → ``(tail_blocks * block_size, 2)``."""
        n = self.tail_blocks * self._block
        out = np.zeros((n, 2))
        for c in range(self._n_channels):
            lt = self._left[c].flush()
            rt = self._right[c].flush()
            out[: lt.size, 0] += lt
            out[: rt.size, 1] += rt
        return out


def convolve_stream(x, ir, block_size: int, *, flush: bool = True) -> np.ndarray:
    """Run a whole signal through a :class:`PartitionedConvolver` (offline helper).

    Streams ``x`` block by block (zero-padding the final partial block) and,
    when ``flush`` is set, appends the filter tail — so the result equals
    ``scipy.signal.fftconvolve(x, ir)`` (length ``len(x) + len(ir) - 1``) to
    floating error. With ``flush=False`` it returns only the first ``len(x)``
    samples (the real-time region, no tail), zero-padded up to a block multiple.

    Convenience for tests and offline rendering; the live path calls
    :meth:`PartitionedConvolver.process_block` directly.
    """
    x = np.asarray(x, dtype=np.float64).ravel()
    conv = PartitionedConvolver(ir, block_size)
    n_blocks = max(1, -(-x.size // block_size))  # ceil, at least one block
    padded = np.zeros(n_blocks * block_size)
    padded[: x.size] = x
    body = np.concatenate(
        [conv.process_block(padded[i * block_size : (i + 1) * block_size]) for i in range(n_blocks)]
    )
    if not flush:
        return body
    return np.concatenate((body, conv.flush()))[: x.size + conv.ir_len - 1]
