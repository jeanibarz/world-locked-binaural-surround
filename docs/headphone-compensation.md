# Compensating your own headphones

The impulse responses the renderer convolves are a **bare** 7.1 → binaural bake:
they carry the room and the speaker directions, but **not** any correction for your
headphones' own frequency response. Every headphone colours sound differently, so
without a correction the render can sound thin or tilted, and — because the EQ is
what lifts the direct sound over the reverb tail — the spatial image can wander
(see the engine's output stage, `rust-engine/src/hp_eq.rs`, and
[`baked-brir-setup.md`](baked-brir-setup.md)). **Headphone compensation** is a
per-ear filter applied once to the final stereo mix that flattens your headphones
back toward neutral.

## What ships, and what it does

The repository includes the **owner's** compensation, tuned for Audeze LCD-GX
headphones — in the source tree at `rust-engine/hp_eq/` (`baked_7p1_rev26_final.txt`
plus its correction FIR under `binaural/audeze_lcdgx_comp-48khz/`). It is an
EqualizerAPO-format configuration applied as a global per-ear chain on the final
L/R buses, *after* the per-speaker convolution and downmix — so it never touches
the world-lock steering, only the timbre. The engine folds in a global headroom
preamp (−20 dB) when it loads the chain, and defaults the stage **on**.

> **Where the config actually is at runtime.** The engine looks for the config
> install-relative at `<install>/hp_eq/` (then `%APPDATA%\WLBS\hp_eq\`), but the
> standalone bundle script (`host/bundle-engine.sh`) does **not** copy `hp_eq/`
> into the bundle. So on a script-built standalone bundle the config is absent and
> the engine logs `HP_EQ_WARN … headphone EQ disabled` until you supply one (see
> the import path below). The owner's Python launcher instead passes it explicitly
> with `--output-eq` (see [`prerequisite-assets.md`](prerequisite-assets.md)).

Because it is tuned for one specific headphone, **it is not neutral on other
headphones**. To make the render neutral on *your* gear you either switch it off
(if your headphones are already equalized elsewhere) or load a profile for your own
headphones.

## In the app: the on/off toggle

In the cockpit **Settings**, under **HEADPHONE COMPENSATION**, there is a switch.
It is **on by default** (the owner listened with compensation enabled). Turn it off
if your headphones are already corrected by other software, or to hear the bare
bake.

> The Settings note mentions picking your headphone **model**. That model picker is
> **not wired yet** — today the toggle only enables or bypasses the single loaded
> profile. Loading a *different* profile is done on disk, as described next.

## How to obtain a profile for your headphones

A compensation profile is a set of filters that undo your headphones' frequency
response. Two common ways to get one:

- **Measure your own.** The shipped profile was produced this way — its comp file is
  a **Room EQ Wizard (REW)** export (`#Room EQ V5.20.5` …), i.e. a list of
  parametric `Filter:` lines plus an optional correction FIR. With a measurement
  microphone and REW you measure your headphones and export the correction in
  **EqualizerAPO** format.
- **Use a published profile for your model.** Many headphones have community
  measurements you can export to EqualizerAPO's `Filter:` format. Use one matched to
  your exact model and pad/firmware where possible.

> **TODO (needs real-host verification).** This repo does not contain a verified,
> step-by-step measurement runbook (exact REW capture settings, mic calibration, or
> a specific published-profile source and its export settings). Treat the two routes
> above as general guidance; the one fact grounded in this repo is the **required
> format** — see below.

## How to load your own profile (the import path)

What the engine actually requires is **an EqualizerAPO-format config that mixes down
to the `L` and `R` buses and carries a per-ear compensation/EQ chain.** The loader
(`rust-engine/src/hp_eq.rs`, `load_output_eq`) parses `Filter:`, `Preamp:`,
`Convolution:` and `Include:` lines, isolates the chain on the final L/R buses, and
applies it. If it finds no L/R chain it logs `HP_EQ_WARN … headphone EQ disabled`
and renders without compensation rather than failing.

### Standalone engine

The engine reads its headphone-EQ config from a **directory**, resolved like the
other assets (CLI flag → environment variable → next to the exe → `%APPDATA%\WLBS`):

| Source | How |
|---|---|
| `--hp-eq-dir <dir>` | command-line flag |
| `WLBS_HP_EQ_DIR` | environment variable |
| `<install>/hp_eq/` | bundled, next to `wlbs_engine.exe` |
| `%APPDATA%\WLBS\hp_eq\` | per-user fallback |

Inside that directory the engine opens a config file named **`baked_7p1_rev26_final.txt`**,
and resolves any `Include:` / `Convolution:` paths **relative to that directory**
(the owner's config keeps its correction FIR under
`binaural/audeze_lcdgx_comp-48khz/`, relative to the config file). So to load your
own profile:

1. Put your EqualizerAPO config — and any FIR WAVs it `Include:`s / convolves — into
   a directory, with the top-level config file named `baked_7p1_rev26_final.txt`.
2. Point the engine at that directory with `--hp-eq-dir <dir>` or
   `WLBS_HP_EQ_DIR`, or drop it in `%APPDATA%\WLBS\hp_eq\`, or replace the bundled
   `hp_eq/` folder.
3. Start the engine and confirm the `HP_EQ loaded …` line (not `HP_EQ_WARN`) in its
   console output.

> **Limitation (and the code follow-up this issue tracks).** There is **no in-app
> picker or import command** for headphone compensation — unlike BRIR packs, which
> have `--import-pack` — and the standalone engine currently looks for the fixed
> filename `baked_7p1_rev26_final.txt`. Loading your own profile therefore means
> placing the file at the resolved path under that name (or overriding the
> directory). Surfacing a cleaner import path is the **code** half of this issue and
> is out of scope for this docs change.

### Python / developer launcher

The Python launcher is more direct: pass **`--output-eq <config.txt>`** pointing
straight at your EqualizerAPO config file (this is what `host/run.cmd` sets via its
`OUTEQ` variable). Omit `--output-eq` to disable compensation entirely.

---

For where these files live alongside the engine's other assets, see
[`prerequisite-assets.md`](prerequisite-assets.md); for how the baked bake and the
compensation config relate, see [`baked-brir-setup.md`](baked-brir-setup.md).
