# 06 — HRIR/SOFA loader (B1.1)

**Date:** 2026-10-16  ·  **Milestone:** M1  ·  **Status:** done

## Question

Can we load a SOFA HRIR set, expose its directions and per-ear impulse
responses, and resample them to the engine rate — the first brick of the static
render engine (M1)?

## Context

M1 turns 7.1 PCM into stereo by convolving each speaker with the HRIR for its
direction (FR-5). That starts with reading HRIRs. They ship as **SOFA** files
(HDF5; the **SimpleFreeFieldHRIR** convention). The sibling
`binaural-localization` already parses SOFA with **h5py** directly
(`Data.IR` (M,2,N), `SourcePosition` (M,3) spherical degrees,
`Data.SamplingRate`), so we mirror that rather than add a dedicated SOFA library.
This is pure numpy/scipy/h5py — no device, runs on WSL/CI.

## Hypothesis

An h5py read gives a usable `HRIRSet`; a great-circle "nearest direction" lookup
handles the azimuth wrap; polyphase resampling converts 44.1 kHz sets to the
48 kHz engine rate; and the ipsi/contra ear mapping can be validated physically
(the ear nearer the source is louder — head shadow).

## Method

- `src/binaural_headtracker/hrir.py`: pure helpers `angular_distance`,
  `nearest_index` (vectorised great-circle), `resample_irs`
  (`scipy.signal.resample_poly`, up/down reduced by GCD), plus the `HRIRSet`
  dataclass (`directions`, `left`, `right`, `samplerate`; `nearest`,
  `nearest_irs`, `ipsi_contra`, `resampled`) and `load_sofa`.
- `h5py>=3.10` added to the **baseline** deps (BSD-3-Clause) — SOFA reading is
  core and cross-platform, not a platform extra.
- Tests (`tests/test_hrir.py`, 12): the pure math directly, and `load_sofa`
  against a **synthetic** SimpleFreeFieldHRIR SOFA generated in a tmp dir (unique
  per-measurement delta IRs) — hermetic, no binary in git. Then a real-world
  check on **MIT KEMAR** (fetched, `.gitignore`d).

## Result

46/46 pytest green on WSL; ruff + format clean. Real set (MIT KEMAR
`mit_kemar_normal_pinna.sofa`):

| Check | Value |
|---|---|
| Measurements / taps / rate | **710** / 512 / 44100 |
| Azimuth / elevation span | 0–355° / −40…+90° |
| Nearest to front (0,0) | symmetric: both ears peak at tap 53 |
| Ipsi vs contra at az 90° | **0.564 vs 0.137** (~4:1 head shadow) |
| Resample 44100 → 48000 | 512 → **558** taps (×160/147), rate 48000 |

## Interpretation

- **Loader works on both synthetic and real sets.** The synthetic round-trip
  pins the parsing (directions, L/R receiver order, rate); KEMAR proves it on a
  real full-sphere set.
- **The ipsi/contra mapping is physically correct.** At az 90° the ipsilateral
  (left) ear is ~4× louder than the contralateral — the head-shadow signature.
  That simultaneously validates the SOFA azimuth convention we assumed (CCW, 90° =
  left) and the `ipsi_contra` side logic; a wrong convention would have put the
  loud ear on the wrong side.
- **Front symmetry** (both ears peak at the same tap for a 0° source) is the
  expected zero-ITD case — a second independent sanity check.
- Reading SOFA directly with h5py keeps dependencies light (no SOFA library) and
  consistent with the sibling. Only nearest-direction lookup is provided now;
  smooth interpolation between measured directions is **B1.2**.

## Dependency licences (§ 9 compliance)

| Item | Licence / terms |
|---|---|
| h5py | BSD-3-Clause (permissive) |
| MIT KEMAR SOFA | public HRTF research data (MIT Media Lab KEMAR) — **fetched by the user, not bundled** (`.gitignore`d) |

No GPL.

## Decision / next

B1.1 done: SOFA → `HRIRSet` with directions, per-ear IRs, nearest lookup, and
engine-rate resampling, validated on MIT KEMAR. Next eligible block: **B1.2**
(direction interpolation — nearest → bilinear/VBAP, ITD-aware — so the engine can
render any (az, el), not just measured points).
