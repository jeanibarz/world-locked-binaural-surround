"""B1.14 deliverable (2): offline approximation error, full vs direct+shared-tail.

Splits each BRIR at (uniform onset D) + 20 ms into direct / tail. Renders a
representative 7.1 scene two ways and compares per-ear spectra + octave-band
levels (journal-41 style, on the real BRIR):

  full   : out_ear = Sum_c conv(x_c, h_c)                 (per-direction full BRIR)
  approx : out_ear = Sum_c conv(x_c, direct_c_ear)        (exact short directs)
                     + conv(Sum_c x_c, h_shared_ear)      (ONE shared late reverb)

The direct term is identical in both; all error is in the tail term, so we also
report the tail-only error. Two scenes bracket the content-correlation range:
  * decorrelated: independent pink noise per directional channel (ambience-like);
  * correlated:   the SAME pink noise fed to all directional channels (phantom).
Shared tail tested: per-ear MEAN of the channel tails (the diffuse average),
and a GEOMETRIC-MEAN-scaled variant (sqrt(N) louder) that splits the
correlated/decorrelated level swing.
"""

from __future__ import annotations

import os
import sys

import numpy as np
from scipy.signal import fftconvolve

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from binaural_headtracker.surround import load_baked_brir  # noqa: E402

BRIR_DIR = (
    "/mnt/c/Program Files/EqualizerAPO/config/"
    "audeze-lcd-gx-binaural-surround/eqapo_config/baked_7p1_test"
)
DIRECT_MS = 20.0
DIR_CH = ("L", "R", "C", "SL", "SR", "RL", "RR")  # directional ring (LFE excluded)
RNG = np.random.default_rng(1234)


def pink(n: int) -> np.ndarray:
    """Pink-ish noise via 1/sqrt(f) shaping of white noise."""
    w = RNG.standard_normal(n)
    X = np.fft.rfft(w)
    f = np.fft.rfftfreq(n, 1.0)
    f[0] = f[1]
    X /= np.sqrt(f)
    x = np.fft.irfft(X, n=n)
    return x / (np.std(x) + 1e-12)


def octave_bands(sr: int):
    centers = np.array([63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000], float)
    edges_lo = centers / np.sqrt(2)
    edges_hi = centers * np.sqrt(2)
    return centers, edges_lo, edges_hi


def band_levels_db(sig: np.ndarray, sr: int) -> np.ndarray:
    n = 1 << int(np.ceil(np.log2(len(sig))))
    X = np.abs(np.fft.rfft(sig, n=n)) ** 2
    f = np.fft.rfftfreq(n, 1.0 / sr)
    _, lo, hi = octave_bands(sr)
    out = []
    for a, b in zip(lo, hi, strict=False):
        m = (f >= a) & (f < b)
        p = X[m].sum() if m.any() else 1e-20
        out.append(10 * np.log10(p + 1e-20))
    return np.array(out)


def render_full(x: dict, brir, ear: str) -> np.ndarray:
    bank = brir.left if ear == "L" else brir.right
    out = None
    for ch, sig in x.items():
        h = bank[brir.index(ch)]
        y = fftconvolve(sig, h)
        out = y if out is None else out + y
    return out


def render_approx(x: dict, direct, shared, brir, ear: str) -> np.ndarray:
    # directs (exact)
    out = None
    for ch, sig in x.items():
        y = fftconvolve(sig, direct[ch][ear])
        out = y if out is None else out + y
    # shared tail on the summed scene
    mix = None
    for sig in x.values():
        mix = sig.copy() if mix is None else mix + sig
    yt = fftconvolve(mix, shared[ear])
    n = max(len(out), len(yt))
    acc = np.zeros(n)
    acc[: len(out)] += out
    acc[: len(yt)] += yt
    return acc


def tail_full(x: dict, tail, brir, ear: str) -> np.ndarray:
    out = None
    for ch, sig in x.items():
        y = fftconvolve(sig, tail[ch][ear])
        out = y if out is None else out + y
    return out


def tail_approx(x: dict, shared, ear: str) -> np.ndarray:
    mix = None
    for sig in x.values():
        mix = sig.copy() if mix is None else mix + sig
    return fftconvolve(mix, shared[ear])


def main() -> None:
    brir = load_baked_brir(BRIR_DIR)
    sr = brir.samplerate
    dsamp = int(round(DIRECT_MS * sr / 1000.0))

    # uniform leading delay D = min energy-threshold onset over all channel-ears
    def eon(ir):
        ce = np.cumsum(ir.astype(np.float64) ** 2)
        return int(np.searchsorted(ce, 0.005 * ce[-1]))
    D = min(min(eon(brir.left[brir.index(c)]), eon(brir.right[brir.index(c)]))
            for c in brir.channels)
    split = D + dsamp
    print(f"uniform onset D = {D} smp ({1000*D/sr:.3f} ms); "
          f"split at {split} smp ({1000*split/sr:.3f} ms); tail = {brir.n_taps-split} smp.\n")

    # per-channel direct / tail
    direct = {}
    tail = {}
    for ch in DIR_CH:
        i = brir.index(ch)
        direct[ch] = {}
        tail[ch] = {}
        for ear, bank in (("L", brir.left), ("R", brir.right)):
            ir = bank[i]
            d = ir.copy()
            t = ir.copy()
            d[split:] = 0.0
            t[:split] = 0.0
            direct[ch][ear] = d[:split]   # trimmed short direct
            tail[ch][ear] = t
    N = len(DIR_CH)

    # shared tails (per ear): mean of channel tails, and geometric-mean-scaled.
    shared_mean = {}
    shared_geo = {}
    for ear in ("L", "R"):
        stack = np.stack([tail[ch][ear] for ch in DIR_CH])  # (N, n_taps)
        m = stack.mean(axis=0)
        shared_mean[ear] = m
        shared_geo[ear] = m * np.sqrt(N)  # splits the corr/decorr level swing

    dur = sr  # 1 s scenes
    scenes = {
        "decorrelated": {ch: pink(dur) for ch in DIR_CH},
        "correlated": (lambda s: {ch: s for ch in DIR_CH})(pink(dur)),
    }

    for sname, x in scenes.items():
        print(f"================= scene: {sname} =================")
        for shname, shared in (("mean-tail", shared_mean), ("geo-mean-tail", shared_geo)):
            print(f"\n  shared tail = {shname}")
            for ear in ("L", "R"):
                yf = render_full(x, brir, ear)
                ya = render_approx(x, direct, shared, brir, ear)
                n = min(len(yf), len(ya))
                yf, ya = yf[:n], ya[:n]
                # broadband level
                lvl_f = 10 * np.log10(np.mean(yf ** 2) + 1e-20)
                lvl_a = 10 * np.log10(np.mean(ya ** 2) + 1e-20)
                # band levels
                bf = band_levels_db(yf, sr)
                ba = band_levels_db(ya, sr)
                dband = ba - bf
                # tail-only error
                tf = tail_full(x, tail, brir, ear)
                ta = tail_approx(x, shared, ear)
                nt = min(len(tf), len(ta))
                tlvl_f = 10 * np.log10(np.mean(tf[:nt] ** 2) + 1e-20)
                tlvl_a = 10 * np.log10(np.mean(ta[:nt] ** 2) + 1e-20)
                centers, _, _ = octave_bands(sr)
                print(f"    ear {ear}: broadband dL = {lvl_a-lvl_f:+.2f} dB | "
                      f"tail-only dL = {tlvl_a-tlvl_f:+.2f} dB")
                print("      band dB(approx-full): " +
                      " ".join(f"{int(c)}:{d:+.1f}" for c, d in zip(centers, dband, strict=False)))
                mad = np.mean(np.abs(dband))
                mxd = np.max(np.abs(dband))
                rms = np.sqrt(np.mean(dband ** 2))
                print(f"      band-level error: mean|d|={mad:.2f} dB "
                      f"max|d|={mxd:.2f} dB rms={rms:.2f} dB")


if __name__ == "__main__":
    main()
