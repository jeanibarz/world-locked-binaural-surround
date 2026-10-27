# Choosing and loading an HRIR set

The renderer makes a virtual speaker sound like it is in a fixed place by filtering
its signal through a **head-related impulse response (HRIR)** — a short recording
of *how sound from that direction reaches your two ears*. This page explains what
an HRIR is in plain language, why the set you use matters, which set to start with,
and how to load one **in the app** (no Python needed).

## What an HRIR is, and why it matters

When a sound comes from, say, your front-left, it reaches your left ear slightly
sooner and louder than your right, and the folds of your outer ear (the *pinna*)
colour it in a direction-specific way. Your brain uses exactly those cues to tell
*where* a sound is. An HRIR captures that transformation for one direction; a full
**HRIR set** has one for every direction around you. The renderer convolves each
virtual speaker with the HRIR for that speaker's direction *relative to your
current head pose*, so when you turn your head the sound stays put in the room —
the project's core "world-locked" effect (see [`../README.md`](../README.md)).

**Why individualization matters.** Pinna shape differs from person to person, so an
HRIR set measured on someone else's head (or a dummy head) is an approximation of
*your* ears. A mismatched set still gives you left/right and world-locking, but
front/back and up/down can be weaker or ambiguous until your brain adapts. A set
closer to your own anatomy externalises more convincingly. Individual HRIR
fine-tuning is on the roadmap but not yet shipped (see [`../README.md`](../README.md)
§ Status).

> **Two kinds of set — know which one you are dealing with.** This project uses two
> related-but-different data formats, loaded by two different paths:
>
> - **BRIR packs** — baked per-speaker *binaural room* impulse responses (a
>   directory of `<ch>_L.wav` / `<ch>_R.wav` for the 7.1 channels). These are what
>   the **standalone engine renders from**, and what the in-app picker selects.
> - **SOFA HRIR sets** — generic, anechoic HRIR databases in the `.sofa` format
>   (e.g. MIT KEMAR). These are read by the **Python/developer path** and used by
>   the blind A/B localization test. The standalone engine does **not** load
>   `.sofa` files directly (see the limitation at the end).

---

## The quick answer

- **Running the standalone engine?** Start with the **bundled `default` pack** — it
  is already installed and selected, and needs nothing from you. If you want to try
  an alternative, import another BRIR pack (below) and pick it in the cockpit.
- **Running the Python/developer path, or the localization test?** Start with
  **MIT KEMAR**, a well-behaved public set. Fetch it and others from
  <https://sofacoustics.org/data/database/> as described in
  [`hrir-setup.md`](hrir-setup.md).

---

## Loading a set in the app (standalone engine)

The cockpit exposes the set as a picker — you never touch Python.

1. Open the cockpit (the engine serves it in your browser) and go to **Settings**.
2. Under **SPATIAL FILTER (HRIR)**, open the dropdown. It lists every BRIR pack the
   engine found at startup: the bundled **default** plus any pack you have imported.
3. Pick a pack. The change applies live.

The dropdown is populated from the engine's pack scan, so a pack only appears there
once it is installed.

### Adding alternative packs

A pack is a directory containing the full baked 7.1 WAV set (`<ch>_L.wav` and
`<ch>_R.wav` for channels `L R C LFE RL RR SL SR`). Install one with the engine's
import command:

```
wlbs_engine.exe --import-pack <path-to-pack-dir> --name my-pack
```

This validates the directory (it must hold the complete WAV set) and copies it into
your per-user pack folder, `%APPDATA%\WLBS\packs\my-pack\`. List what is installed:

```
wlbs_engine.exe --list-packs
```

After importing, `my-pack` appears in the Settings dropdown. The layout of a pack
and how the loader reads it are in [`baked-brir-setup.md`](baked-brir-setup.md); for
where packs live on disk and how asset paths resolve, see
[`prerequisite-assets.md`](prerequisite-assets.md).

---

## Using a SOFA set (Python / developer path)

The Python path loads generic `.sofa` HRIR sets directly. A good default is **MIT
KEMAR** (710 directions, a standard SimpleFreeFieldHRIR SOFA file); alternatives
(ARI, SADIE II, RIEC, …) live at <https://sofacoustics.org/data/database/> and most
follow the same convention. The fetch command, the on-disk location (`data/hrir/`),
and the loading API (`load_sofa`, `nearest_irs`) are documented in
[`hrir-setup.md`](hrir-setup.md). The owner's host keeps candidate SOFA sets in a
`hrir_sets\` folder next to `run.cmd`, which `run.cmd test` loads for the blind A/B
and localization test (see [`../host/README.md`](../host/README.md)).

---

## Known limitation — SOFA sets in the standalone engine

The standalone engine renders from **baked BRIR packs only**; it does not yet read
generic `.sofa` HRIR sets, and there is no in-app step to convert a SOFA set into a
pack. So today, "choosing an HRIR set in the app" means choosing among installed
**BRIR packs**, not loading an arbitrary SOFA file.

> **TODO.** A SOFA-aware in-app picker (the "future in-app picker" noted in the
> issue), and the direction interpolation that makes an arbitrary SOFA set usable
> for the head-tracked path, are tracked as follow-up work — see the B1.2
> interpolation work and the research journal
> ([`research-journal/`](research-journal/README.md)). Until then, use the Python
> path (above) for SOFA sets.
