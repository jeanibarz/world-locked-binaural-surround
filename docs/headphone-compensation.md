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

## In the app: the on/off toggle and the model picker

In the cockpit **Settings**, under **HEADPHONE COMPENSATION**, there is a switch
and a **model picker**.

- The **switch** is **on by default** (the owner listened with compensation
  enabled). Turn it off if your headphones are already corrected by other software,
  or to hear the bare bake.
- The **model picker** lists the headphone-comp *profiles* installed on your
  machine. The bundled owner profile appears as **`default`**; any profiles you
  import (see below) appear by their folder name. Choosing one makes the engine load
  and apply that profile **live** — no restart, and with no audible click (the
  switch's click-free enable/bypass path is reused for the swap). The selection is
  saved and restored on the next launch; if a saved profile has since been removed,
  the engine logs `HP_EQ_WARN …` and falls back to `default` (or, if there is no
  `default` either, renders without compensation) rather than failing.

The picker only lists profiles that exist on disk, so to pick *your* headphones you
first install a profile for them, as described next.

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

### Standalone engine — the profile store

Headphone-comp profiles work just like BRIR packs. A **profile** is a directory
holding an EqualizerAPO config plus any FIR WAVs it `Include:`s / convolves, and it
lives under a per-user store:

| | Location |
|---|---|
| Bundled **`default`** profile | the install-relative `hp_eq/` dir (`--hp-eq-dir <dir>` → `WLBS_HP_EQ_DIR` → `<install>/hp_eq/` → `%APPDATA%\WLBS\hp_eq\`) |
| Your imported profiles | `%APPDATA%\WLBS\hp_eq\<name>\` — one subdirectory per profile |

Inside a profile directory the engine opens a config file named **`config.txt`**
(the owner's bundled profile keeps its historical name `baked_7p1_rev26_final.txt`,
which is also accepted), and resolves any `Include:` / `Convolution:` paths
**relative to that directory** (the owner's config keeps its correction FIR under
`binaural/audeze_lcdgx_comp-48khz/`, relative to the config file).

**Import a profile (the `--import-pack` analogue):**

```
wlbs_engine.exe --import-hp-eq <path-to-profile-dir> [--name my-cans]
```

This copies the directory into `%APPDATA%\WLBS\hp_eq\<name>` (the name defaults to
the source folder's basename). The source must contain a top-level config file named
`config.txt` (name your EqualizerAPO export that inside the folder). List what is
installed with:

```
wlbs_engine.exe --list-hp-eq
```

Once imported, the profile shows up in the cockpit's model picker and switches live
when you select it — no file juggling and no restart. You can still drive the
`default` profile's location directly with `--hp-eq-dir <dir>` / `WLBS_HP_EQ_DIR` for
a one-off, and the engine prints a `HP_EQ loaded profile "<name>" …` line (not
`HP_EQ_WARN`) on its console when a profile loads.

### Python / developer launcher

The Python launcher is more direct: pass **`--output-eq <config.txt>`** pointing
straight at your EqualizerAPO config file (this is what `host/run.cmd` sets via its
`OUTEQ` variable). Omit `--output-eq` to disable compensation entirely.

---

For where these files live alongside the engine's other assets, see
[`prerequisite-assets.md`](prerequisite-assets.md); for how the baked bake and the
compensation config relate, see [`baked-brir-setup.md`](baked-brir-setup.md).
