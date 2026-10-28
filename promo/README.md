# Promo explainer video — working folder

A LinkedIn **explainer video** (+ companion article) for the parent project
**World-Locked Binaural Surround** (head-tracked binaural renderer for Windows).
This folder holds everything needed to **resume the work from scratch** if context
is lost: the scripts that generate every clip, the voiceover texts, the plan, and
the decisions log.

> **Resume in 60 seconds:** read this file, then `notes/STATUS.md` (what's done /
> next) and `notes/DECISIONS.md` (locked choices + accuracy caveats). The rendered
> videos live in the operator's Windows `Videos\` folder and are **regenerable**
> from `scripts/`.

---

## What this is

A ~3–5 min pedagogical promo, English, neural‑TTS voiceover, in a **cockpit‑derived
house style**. Two visual registers, stitched into one system:
- **Animated slides** (Pillow) for abstract ideas — one object on screen, smooth motion.
- **Focused real‑UI punch‑ins** (ffmpeg `zoompan`) into a single cockpit panel, synced to the voice.
Plus **real‑data science plots** (matplotlib, dark theme) built from measured HRTFs.

Companion **LinkedIn article** draft lives in the plan (see below).

## Current direction (as of last session)
Moving to a **payoff‑first / Veritasium structure**: open on the finished result
("with just a webcam I made externalized, world‑fixed 3D sound on normal headphones"),
then *"but how? you need the psychoacoustics first — don't move, 3 minutes"*, then the
crash course (stereo → binaural → ITD → spectral fingerprint → cone of confusion →
individuality), then back to the payoff (why head‑tracking, how it's built) → CTA.
See `notes/STORYBOARD.md`.

---

## Where things are

| | |
|---|---|
| **Plan / storyboard / script / LinkedIn article** (rich HTML) | `plan/promo-plan.html` — also published as a claude.ai artifact: **https://claude.ai/artifact/VFhxwA1dcoqDYCaPnmjyd5** |
| **Motion‑graphics engine** (Pillow) | `scripts/anim.py` — renders beats b1,b2,b3,b5,b6 and the `itd` wavefront |
| **Real‑data still plots** | `scripts/hrtf_plots.py` |
| **Real‑data animations** | `scripts/hrtf_anim.py` (ITD curve, spectra morph, individuality), `scripts/hrtf_anim2.py` (ITD sweep rework, cone of confusion 3D) |
| **Voiceover (TTS)** | `scripts/tts_gen.py` + texts in `vo/b1.txt … b6.txt` |
| **Webcam blur mask** | `scripts/mkmask.py` |
| **Assembly / build scripts** | `scripts/build_*.sh` (see table in STATUS) |
| **Review material** | `notes/comm_review_doc.md` (6‑lens comm review input), `notes/script_v1.md` (first draft + accuracy anchors) |
| **Rendered outputs** | operator's `C:\Users\ibarz\Videos\world-locked-*.mp4` (regenerable) |

## Inputs / dependencies (NOT in this repo)
- **Source screen‑capture:** `C:\Users\ibarz\Videos\2026-10-28 18-53-47.mkv`
  (8:47, 3740×2160, 30fps). B‑roll timecodes were picked by motion analysis
  (head‑turn / radar‑sweep windows): ~02:00, 03:05, 03:35, 05:35, 06:05, 07:45; calm UI 00:10–01:05.
- **Real HRTFs:** `/home/jean/git/binaural-localization/data/ircam_sofa/IRC_*_R_44100.sofa`
  (50 IRCAM LISTEN subjects, 187 directions, 8192‑tap @ 44.1 kHz, ears at ±9 cm).
  Read with `h5py`. Use that repo's venv python for sci libs:
  `/home/jean/git/binaural-localization/.venv/bin/python` (matplotlib, scipy, h5py, sofar).
- **TTS:** `edge-tts` **>= 7.3.1** (7.0.0 returns HTTP 403 on synth; upgrade with
  `python3 -m pip install -U --user edge-tts`). Voice `en-US-AndrewNeural`, rate `-4%`.
  The synth endpoint sometimes returns `NoAudioReceived` — the generator retries.
- **Fonts:** DejaVu (Sans‑Bold, SansMono‑Bold) at `/usr/share/fonts/truetype/dejavu/`.
- **GPU:** RTX 3090 (~20 GB free) — not actually needed; all rendering is CPU (Pillow/matplotlib/ffmpeg).

## ⚠ Known gotcha: hardcoded paths
The `scripts/*.sh` and `*.py` hardcode the **ephemeral session scratchpad**
`/tmp/claude-1000/.../scratchpad/cut` as their working dir (where frames/clips are
written). That path **will not exist** in a new session. To rebuild: pick a fresh
WORKDIR, copy these scripts there, and update the `cd`/`OUT`/path constants, or
sed‑replace the old scratchpad path. (TODO: parameterize via an env var — not yet done.)

## How to rebuild (quick reference)
From a chosen WORKDIR holding the scripts + `vo/*.txt` (and after fixing paths):
```
# 1. voices (needs network; edge-tts >=7.3.1)
python3 tts_gen.py                      # -> vo1.mp3 … vo6.mp3
# 2. animated beats (Pillow)
python3 anim.py b1 f_b1 <dur1> ; …      # dur = ffprobe duration of voN.mp3
# 3. real-data clips (BL venv python)
<BLvenv>/python hrtf_plots.py           # stills
<BLvenv>/python hrtf_anim.py            # ITD curve / spectra morph / individuality frames
<BLvenv>/python hrtf_anim2.py           # ITD sweep rework + cone of confusion
# 4. encode + assemble  (see build_*.sh for exact ffmpeg filtergraphs:
#    zoompan punch-ins, portrait blur via alphamerge+mask, deterministic aeval stereo pan,
#    loudnorm -14 LUFS, concat demuxer)
```
See `notes/STATUS.md` for which build script produces which output.
