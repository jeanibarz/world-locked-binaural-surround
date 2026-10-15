# Baked 7.1 BRIR set — the owner's Audeze LCD-GX surround

Besides generic SOFA sets (`docs/hrir-setup.md`), the engine can render from a
**baked per-speaker BRIR set** — the owner's hand-tuned EqualizerAPO binaural
surround. This is the data for the **static (fixed-head)** renderer
(`binaural_headtracker.surround`); the head-tracked path needs a continuous field
(see `docs/hrir-setup.md` and journal 06/07).

## What the set is

8 fixed 7.1 speaker directions, each a stereo **binaural room impulse response**
(one IR per ear). Source files live in the EqualizerAPO config repo
`audeze-lcd-gx-binaural-surround`:

- `eqapo_config/baked_7p1_test/<CH>_L.wav`, `<CH>_R.wav` — the baked BRIRs the
  final config convolves (500 ms, 48 kHz, float32), for channels
  `L R C LFE RL RR SL SR` (Microsoft 7.1 order).
- `eqapo_config/binaural/blocked_ear_1000ms-48khz/` — the raw 1000 ms
  blocked-ear BRIRs behind the bake.

The config applies a headphone-comp FIR and a subjective parametric EQ **after**
the convolve-and-sum; the renderer reproduces the convolve-and-sum stage only —
the EQ chain is the live-EQ milestone (M5 / B5).

These `.wav` files are **personal data** and are **not** committed (`.gitignore`:
`*.wav`). Point the loader at your own copy.

## Loading and rendering

```python
import numpy as np
from binaural_headtracker.surround import load_baked_brir, render_7p1_to_stereo

brir = load_baked_brir(
    r"C:/Program Files/EqualizerAPO/config/"
    r"audeze-lcd-gx-binaural-surround/eqapo_config/baked_7p1_test"
)
# pcm: (M, 8) float, columns in L R C LFE RL RR SL SR order
stereo = render_7p1_to_stereo(pcm, brir)  # (M + N - 1, 2)
```

`load_baked_brir(directory, channels=..., left_suffix="_L", right_suffix="_R")`
reads `<channel><suffix>.wav` per channel, requires one shared sample rate, and
zero-pads IRs to a common length. Channel/ear order and the convolve-and-sum are
exactly the EqualizerAPO baked config's.
