# HRIR sets — fetching and loading

The render engine convolves each virtual speaker with a **head-related impulse
response (HRIR)** for the speaker's direction relative to the head. HRIR sets are
distributed as **SOFA** files (an HDF5 container; the **SimpleFreeFieldHRIR**
convention). This project reads them with `binaural_headtracker.hrir.load_sofa`
— no dedicated SOFA library, just `h5py` (the same approach as the sibling
`binaural-localization`).

SOFA files are large and are **not** committed (`.gitignore`: `*.sofa`, `data/`).
Fetch one into `data/hrir/` yourself.

## A good default: MIT KEMAR

```bash
mkdir -p data/hrir
curl -L -o data/hrir/mit_kemar_normal_pinna.sofa \
  https://sofacoustics.org/data/database/mit/mit_kemar_normal_pinna.sofa
```

This set has 710 directions (azimuth 0–355°, elevation −40…+90°), 512 taps at
44.1 kHz. Other public sets live at <https://sofacoustics.org/data/database/>
(ARI, SADIE II, RIEC, …) and most follow the same convention.

## Loading

```python
from binaural_headtracker.hrir import load_sofa

hrir = load_sofa("data/hrir/mit_kemar_normal_pinna.sofa", target_rate=48000)
print(hrir.n_measurements, hrir.n_taps, hrir.samplerate)  # 710 558 48000
left_ir, right_ir = hrir.nearest_irs(az=30, el=0)  # nearest direction
ipsi, contra = hrir.ipsi_contra(az=90, el=0)  # ear nearer the source
```

- `directions` is `(M, 2)` = azimuth `[0, 360)` / elevation, degrees. **Azimuth is
  counter-clockwise from front** (0° = front, 90° = left), the standard SOFA
  convention — verified on KEMAR, where az 90° makes the left (ipsilateral) ear
  ~4× louder than the right.
- `target_rate` resamples the IRs to the engine rate (polyphase, exact ratio).
- `nearest_irs` returns the closest measured direction; smooth interpolation
  between directions is **B1.2**.

## Convention notes

- `Data.IR` must be `(M, 2, N)` with receiver 0 = left ear, 1 = right ear; other
  receiver counts raise `ValueError`.
- `SourcePosition` is read as spherical `(az, el, radius)` degrees; a `cartesian`
  `SourcePosition.Type` is converted automatically.
