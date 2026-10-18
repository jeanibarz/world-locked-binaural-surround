"""B1.14 (1b): true onset — the 10 ms search bound clips it; widen and re-measure.

First pass showed onsets pinned at ~479 smp (= the 10 ms bound) and ~0 energy
before 10 ms, so the real direct arrives AFTER 10 ms. Here we:
  * find the true onset with estimate_delay over the first 30 ms (unclipped);
  * cross-check with an energy-threshold onset (first sample where cumulative
    energy crosses 0.5% of total) and the raw |ir| / Hilbert-envelope peak;
  * recompute the 20 ms direct-energy fraction with an ONSET-AWARE window.
"""

from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from binaural_headtracker.interpolate import estimate_delay  # noqa: E402
from binaural_headtracker.surround import load_baked_brir  # noqa: E402

BRIR_DIR = (
    "/mnt/c/Program Files/EqualizerAPO/config/"
    "audeze-lcd-gx-binaural-surround/eqapo_config/baked_7p1_test"
)
DIRECT_MS = 20.0
SEARCH_MS = 30.0  # wide enough to contain the real direct


def energy_onset(ir: np.ndarray, frac: float = 0.005) -> int:
    """First sample where cumulative energy crosses `frac` of total."""
    ce = np.cumsum(ir.astype(np.float64) ** 2)
    tot = ce[-1]
    if tot <= 0:
        return 0
    return int(np.searchsorted(ce, frac * tot))


def main() -> None:
    brir = load_baked_brir(BRIR_DIR)
    sr = brir.samplerate
    dsamp = int(round(DIRECT_MS * sr / 1000.0))
    ssamp = int(round(SEARCH_MS * sr / 1000.0))

    print(f"direct window {DIRECT_MS} ms = {dsamp} smp; onset search over first "
          f"{SEARCH_MS} ms = {ssamp} smp.\n")

    print(f"{'ch':<4}{'ear':<4}{'env_peak_ms':>12}{'E0.5%_ms':>10}{'argmax|ir|_ms':>14}"
          f"{'frac20(onset)':>15}{'frac20(from0)':>15}")
    env_onsets = []
    e_onsets = []
    fracs_onset = []
    fracs_from0 = []
    for ch in brir.channels:
        i = brir.index(ch)
        for ear, bank in (("L", brir.left), ("R", brir.right)):
            ir = bank[i]
            total = float(np.sum(ir ** 2))
            # estimate_delay over a wide window (the Hilbert env peak)
            on_env = estimate_delay(ir[:ssamp])
            # energy-threshold onset
            on_e = energy_onset(ir)
            # raw |ir| argmax within search
            raw = int(np.argmax(np.abs(ir[:ssamp])))
            s0 = max(0, int(np.floor(on_env)))
            e_onset_win = float(np.sum(ir[s0:s0 + dsamp] ** 2))
            e_from0 = float(np.sum(ir[:dsamp] ** 2))
            f_onset = e_onset_win / total
            f_from0 = e_from0 / total
            env_onsets.append(on_env)
            e_onsets.append(on_e)
            fracs_onset.append(f_onset)
            fracs_from0.append(f_from0)
            print(f"{ch:<4}{ear:<4}{1000*on_env/sr:>12.3f}{1000*on_e/sr:>10.3f}"
                  f"{1000*raw/sr:>14.3f}{f_onset*100:>14.2f}%{f_from0*100:>14.2f}%")

    env_onsets = np.array(env_onsets)
    e_onsets = np.array(e_onsets)
    fracs_onset = np.array(fracs_onset)
    fracs_from0 = np.array(fracs_from0)

    print("\n--- onset distribution (estimate_delay over first 30 ms) ---")
    print(f"min  {env_onsets.min():.2f} smp ({1000*env_onsets.min()/sr:.3f} ms)")
    print(f"max  {env_onsets.max():.2f} smp ({1000*env_onsets.max()/sr:.3f} ms)")
    print(f"mean {env_onsets.mean():.2f} smp ({1000*env_onsets.mean()/sr:.3f} ms)")
    print(f"std  {env_onsets.std():.2f} smp ({1000*env_onsets.std()/sr:.3f} ms)")
    print(f"spread (max-min) {env_onsets.max()-env_onsets.min():.2f} smp "
          f"({1000*(env_onsets.max()-env_onsets.min())/sr:.3f} ms)")

    print("\n--- energy-threshold onset (0.5% cumulative) ---")
    print(f"min {e_onsets.min()} smp ({1000*e_onsets.min()/sr:.3f} ms); "
          f"max {e_onsets.max()} smp ({1000*e_onsets.max()/sr:.3f} ms); "
          f"spread {e_onsets.max()-e_onsets.min()} smp "
          f"({1000*(e_onsets.max()-e_onsets.min())/sr:.3f} ms)")

    print("\n--- 20 ms direct-energy fraction (all 16 channel-ears) ---")
    print(f"onset-aware window : min {fracs_onset.min()*100:.2f}%  "
          f"mean {fracs_onset.mean()*100:.2f}%  max {fracs_onset.max()*100:.2f}%")
    print(f"fixed from sample 0: min {fracs_from0.min()*100:.2f}%  "
          f"mean {fracs_from0.mean()*100:.2f}%  max {fracs_from0.max()*100:.2f}%")

    # If we subtract a uniform constant delay D (min onset) then a 20 ms window:
    D = int(np.floor(env_onsets.min()))
    frac_uniform = []
    for ch in brir.channels:
        i = brir.index(ch)
        for bank in (brir.left, brir.right):
            ir = bank[i]
            total = float(np.sum(ir ** 2))
            frac_uniform.append(float(np.sum(ir[D:D + dsamp] ** 2)) / total)
    frac_uniform = np.array(frac_uniform)
    print(f"\n--- (a) uniform-delay subtract D={D} smp ({1000*D/sr:.3f} ms), then 20 ms ---")
    print(f"min {frac_uniform.min()*100:.2f}%  mean {frac_uniform.mean()*100:.2f}%  "
          f"max {frac_uniform.max()*100:.2f}%")

    # Where does the energy sit relative to each onset? cumulative past the onset.
    print("\n--- cumulative energy fraction measured FROM each ear's onset (ms after onset) ---")
    marks = [2, 5, 10, 15, 20, 30, 50]
    print("ch  ear " + "".join(f"{m:>7}ms" for m in marks))
    for ch in brir.channels:
        i = brir.index(ch)
        for ear, bank in (("L", brir.left), ("R", brir.right)):
            ir = bank[i]
            s0 = max(0, int(np.floor(estimate_delay(ir[:ssamp]))))
            ce = np.cumsum(ir[s0:].astype(np.float64) ** 2)
            tot = float(np.sum(ir ** 2))
            cells = []
            for m in marks:
                s = min(len(ce) - 1, int(round(m * sr / 1000.0)))
                cells.append(f"{100*ce[s]/tot:>8.2f}")
            print(f"{ch:<4}{ear:<4}" + "".join(cells))


if __name__ == "__main__":
    main()
