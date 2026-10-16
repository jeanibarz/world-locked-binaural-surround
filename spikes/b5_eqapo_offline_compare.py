"""B5 spike — offline EqualizerAPO vs our renderer: does the comp+EQ post-stage
change the spatial image, or only the timbre?

Context. The owner heard a rendered 7.1 snare clip "move" near the end, while the
*same* clip through EqualizerAPO did not, and asked us to compare our render to
EqualizerAPO's full chain offline. A previous session *hypothesised* the missing
comp+EQ stage stabilises the image (its big HF boost favouring the direct sound).
This spike tests that hypothesis against ground truth — and refutes it.

What it does (all offline, LTI, no audio device):

1. Reuses the sibling project's faithful EqualizerAPO engine
   (``binaural_localization.eqapo``; AGENTS.md: *depend on it, don't duplicate
   it*) to reconstruct the effective 8x2 binaural IR of three cut points of the
   owner's config ``baked_7p1_rev26_final.txt``:
     - **bake**       — convolution + per-ear sum only (== our ``surround`` B1.6);
     - **bake+comp**  — plus the per-ear headphone-compensation FIR + EQ;
     - **full**       — plus the ~31-filter subjective parametric EQ (``Channel: L R``).
2. **Faithfulness check**: the engine's *bake* IR must equal our loaded
   ``baked_7p1_test`` BRIR set bit-for-bit — i.e. our ``render_7p1_to_stereo`` is
   exactly EqualizerAPO's bake stage (settles the "my render differs" worry).
3. **Interaural-symmetry check**: for each stage transition, how much the L-ear
   change differs from the R-ear change (dB). ~0 => the stage only re-timbres both
   ears equally and *cannot* move the image; large => it alters the L/R geometry.
4. **Image-stability metric**: windowed interaural cross-correlation (ITD lag +
   peak coherence) over each snare clip, bake vs full.
5. Regenerates stereo audition WAVs (bake-only and full-chain, with a 0.5 s
   raised-cosine tail fade that tames the exposed reverb tail) for A/B listening.

Requires: the sibling repo (``--sibling-src``) and its eqapo deps
(numpy/scipy/soundfile), plus the owner's EqualizerAPO config + BRIRs + 7.1 test
clips (personal, kept out of git — host paths below). Run, e.g.:

    cd ../binaural-localization && uv run --no-sync python \
        ../binaural-headtracker/spikes/b5_eqapo_offline_compare.py
"""

from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import numpy as np

warnings.simplefilter("ignore")

BASE = "/mnt/c/Program Files/EqualizerAPO/config/audeze-lcd-gx-binaural-surround"
POSITIONS = [
    "FrontCenter", "FrontLeft", "SideLeft", "RearLeft", "RearRight", "SideRight", "FrontRight",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", default=f"{BASE}/eqapo_config/baked_7p1_rev26_final.txt")
    p.add_argument("--brir-dir", default=f"{BASE}/eqapo_config/baked_7p1_test")
    p.add_argument("--clips-dir", default=f"{BASE}/test_localization")
    p.add_argument("--out-dir", default="/mnt/c/Users/ibarz/Downloads/bht_static_render_test")
    sibling = Path(__file__).resolve().parents[2] / "binaural-localization" / "src"
    p.add_argument("--sibling-src", default=str(sibling))
    p.add_argument("--repo-src", default=str(Path(__file__).resolve().parents[1] / "src"))
    p.add_argument("--fs", type=int, default=48000)
    p.add_argument("--ir-length", type=int, default=1 << 16)
    p.add_argument("--no-wav", action="store_true", help="skip writing audition WAVs")
    return p.parse_args()


def windowed_itd_coh(stereo, fs, win=1024, hop=512, thresh_db=-40.0):
    """Per-window peak-normalised interaural xcorr → (ITD lag[samples], coherence)."""
    from scipy.signal import fftconvolve

    left, right = stereo[:, 0], stereo[:, 1]
    peak = np.max(np.abs(stereo)) + 1e-12
    lags, cohs = [], []
    for s in range(0, len(left) - win, hop):
        lseg, rseg = left[s : s + win], right[s : s + win]
        energy = np.sqrt((lseg @ lseg + rseg @ rseg) / win)
        if 20 * np.log10(energy / peak + 1e-12) < thresh_db:
            continue
        l0, r0 = lseg - lseg.mean(), rseg - rseg.mean()
        denom = np.sqrt((l0 @ l0) * (r0 @ r0)) + 1e-12
        xc = fftconvolve(l0, r0[::-1])
        mid, maxlag = len(xc) // 2, 64  # +/-1.3 ms covers head ITD
        seg = xc[mid - maxlag : mid + maxlag + 1] / denom
        k = int(np.argmax(np.abs(seg)))
        lags.append(k - maxlag)
        cohs.append(float(np.abs(seg[k])))
    return np.array(lags), np.array(cohs)


def tail_fade(stereo, fs, seconds=0.5):
    """Raised-cosine fade over the last `seconds` (tames the exposed reverb tail)."""
    n = min(int(seconds * fs), stereo.shape[0])
    ramp = 0.5 * (1 + np.cos(np.linspace(0, np.pi, n)))
    out = stereo.copy()
    out[-n:] *= ramp[:, None]
    return out


def main() -> int:
    args = parse_args()
    sys.path.insert(0, args.repo_src)
    sys.path.insert(0, args.sibling_src)

    from binaural_localization.eqapo.chain import CHANNELS_71, ChainSimulator, parse_config
    from scipy.io import wavfile
    from scipy.signal import fftconvolve

    from binaural_headtracker.surround import load_baked_brir

    fs, length = args.fs, args.ir_length
    cmds = parse_config(args.config)
    bake_end = max(
        i
        for i, c in enumerate(cmds)
        if c.kind == "copy" and str(c.payload).strip().startswith("L=")
    )
    comp_end = next(
        i
        for i, c in enumerate(cmds)
        if i > bake_end and c.kind == "channel" and str(c.payload).strip().lower() == "all"
    )
    subsets = {"bake": cmds[: bake_end + 1], "bake+comp": cmds[: comp_end + 1], "full": cmds}
    print(f"config: {len(cmds)} commands  (bake ends @ {bake_end}, comp @ {comp_end})")

    def eff_brir(cmdlist):
        irs = np.zeros((8, 2, length))
        for i, ch in enumerate(CHANNELS_71):
            sim = ChainSimulator(fs=fs, length=length)
            for c in CHANNELS_71:
                sim.signals[c] = np.zeros(length)
            sim.signals[ch][0] = 1.0
            sim.run(cmdlist)
            irs[i, 0] = sim.signals.get("L", np.zeros(length))
            irs[i, 1] = sim.signals.get("R", np.zeros(length))
        return irs

    br = {k: eff_brir(v) for k, v in subsets.items()}

    # (2) faithfulness: engine's bake == our loaded BRIR set
    brir = load_baked_brir(args.brir_dir)
    err = 0.0
    for i in range(8):
        n = min(brir.n_taps, length)
        err = max(
            err,
            float(np.max(np.abs(br["bake"][i, 0, :n] - brir.left[i, :n]))),
            float(np.max(np.abs(br["bake"][i, 1, :n] - brir.right[i, :n]))),
        )
    print(f"\nfaithfulness |engine bake - our BRIR|max = {err:.2e}  (~0 => render == EQ-APO bake)")

    # (3) interaural symmetry per stage transition
    print("\ninteraural-image change per stage (mean |L-ear dB change - R-ear dB change|):")
    for a, b in [("bake", "bake+comp"), ("bake+comp", "full")]:
        asym = []
        for i in range(8):
            fal = np.abs(np.fft.rfft(br[a][i, 0])) + 1e-9
            fbl = np.abs(np.fft.rfft(br[b][i, 0])) + 1e-9
            far = np.abs(np.fft.rfft(br[a][i, 1])) + 1e-9
            fbr = np.abs(np.fft.rfft(br[b][i, 1])) + 1e-9
            asym.append(np.mean(np.abs(20 * np.log10(fbl / fal) - 20 * np.log10(fbr / far))))
        changes = np.mean(asym) >= 0.5
        tag = "CHANGES the interaural image" if changes else "timbre only, image unchanged"
        print(f"  {a:9} -> {b:9}: {np.mean(asym):5.2f} dB  ({tag})")

    # (4) image-stability metric, bake vs full
    def render(pcm, irs):
        out = np.zeros((pcm.shape[0] + length - 1, 2))
        for i in range(8):
            for e in (0, 1):
                out[:, e] += fftconvolve(pcm[:, i], irs[i, e])
        return out

    print(
        f"\n{'position':12} {'bake ITDstd':>11} {'full ITDstd':>11} | "
        f"{'bake coh':>8} {'full coh':>8}"
    )
    agg = {"bake": [[], []], "full": [[], []]}
    renders = {}
    for pos in POSITIONS:
        _, raw = wavfile.read(f"{args.clips_dir}/drum_{pos}.wav")
        pcm = np.asarray(raw, dtype=np.float64)
        if np.issubdtype(np.asarray(raw).dtype, np.integer):
            pcm /= 32768.0
        rb, rf = render(pcm, br["bake"]), render(pcm, br["full"])
        renders[pos] = (rb, rf)
        lb, cb = windowed_itd_coh(rb, fs)
        lf, cf = windowed_itd_coh(rf, fs)
        agg["bake"][0] += list(lb)
        agg["bake"][1] += list(cb)
        agg["full"][0] += list(lf)
        agg["full"][1] += list(cf)
        print(
            f"{pos:12} {np.std(lb):11.1f} {np.std(lf):11.1f} | "
            f"{np.mean(cb):8.3f} {np.mean(cf):8.3f}"
        )
    print("\nmeans:  "
          f"ITDstd bake={np.std(agg['bake'][0]):.1f} full={np.std(agg['full'][0]):.1f}  "
          f"(lower=steadier image) | "
          f"coh bake={np.mean(agg['bake'][1]):.3f} full={np.mean(agg['full'][1]):.3f}")

    # (5) audition WAVs
    if not args.no_wav:
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        peak = max(np.max(np.abs(r)) for pair in renders.values() for r in pair) + 1e-12
        scale = 0.94 / peak

        def to_i16(x):
            return (np.clip(x * scale, -1.0, 1.0) * 32767.0).astype(np.int16)

        for pos, (rb, rf) in renders.items():
            wavfile.write(str(out_dir / f"cmp_bake_{pos}.wav"), fs, to_i16(tail_fade(rb, fs)))
            wavfile.write(str(out_dir / f"cmp_full_{pos}.wav"), fs, to_i16(tail_fade(rf, fs)))
        print(f"\nwrote {2 * len(renders)} audition WAVs (cmp_bake_* / cmp_full_*) to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
