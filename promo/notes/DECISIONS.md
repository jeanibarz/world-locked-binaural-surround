# Decisions & accuracy caveats (locked unless changed by the operator)

## Format / delivery
- **Language:** English (video VO + subtitles + LinkedIn article).
- **Voice:** neural TTS, `edge-tts` voice **en-US-AndrewNeural**, rate **-4%**. (Operator may re-record in their own voice instead.)
- **Length:** evolving. Base styled cut ≈ **2:26**. Adding the HRTF crash course (+~70s)
  and the payoff-first opening → target **~4–5 min** explainer, plus a short **teaser** cut for the feed.
- **Captions:** burned in throughout (most LinkedIn views are muted).
- **Audio:** VO + ducked music bed (TODO), normalise **-14 LUFS**.
- **Companion LinkedIn article:** English, ~800–1000 words, embeds the video. Draft in `plan/promo-plan.html`.

## Accuracy caveats — MUST hold in any script/visual (fact-checked vs parent repo)
- **Externalisation is a HYPOTHESIS, not a measured result.** No human A/B test exists
  (parent `requirements §1` calls it "the perceptual hypothesis"; milestone M7 not started).
  Never state it as proven. Use "the bet / the hypothesis I'm testing".
- **NOT open-source.** Repo is all-rights-reserved. Say **"built in public"**, never "open source" / "in the open".
- **Head tracker = orientation only, ~±45°.** Say **"3-DoF (orientation)"** — yaw/pitch/roll.
  (If translation is ever tracked, it becomes 6-DoF — confirm with operator.)
- **Rust kernel ~5× real time on CPU, GPU optional.**
- **HRTF** = the single filter bundling the time/level/spectral cues — it gives **direction, not distance**.
  Do NOT present HRTF as a third cue beside ITD/ILD.
- **Smyth Realiser / multi-thousand-$ comparison = aspiration ("the goal"), not achieved parity.**

## Plumbing (corrected by operator, session 2026-10-28)
- Audio in: **VB-Audio Cable** (ships with VoiceMeeter) — a third-party virtual device the user installs.
- Audio out: **the normal Windows sound device**. **No custom driver.**
- **No head-motion prediction** (operator finds it does more harm than good).
- **Latency topic is REMOVED** from the video and article (operator's call — too complex, low payoff).
  Real end-to-end latency with the 60 fps webcam is ~**60 ms**; kept **off-screen**.
  (Earlier drafts had MME→WASAPI 90→20 ms + α-β prediction + ~40 ms floor — all **obsolete, do not reuse**.)

## Visual identity
- Palette: bg `#0a0e14`, panel `#121b24`, accent cyan `#35e0d0`, "bad/warning" amber `#f5b556`,
  ink `#e8eef0`, muted `#8a9aa3`, grid `#1e2a33`. Mono labels, Sans-Bold headings.
- Two registers (clean animated slides + focused real-UI punch-ins) cohered by a faint dot-grid + vignette.
- Webcam punch-in uses **portrait background blur** (gblur + feathered central ellipse mask via alphamerge) + vignette,
  because the raw cockpit full-screen is too busy and shows the operator's room.

## Audio-demo technique (Veritasium-style "hear it while you see it")
- Stereo L↔R pan is **deterministic** via ffmpeg `aeval` (constant-power pan from the SAME sine as the on-screen dot;
  right = right channel). Verified correct: corr((R²−L²), intended_pan) = **+0.94**.
- **Do NOT use `apulsator`** — its LFO phase/polarity is unpredictable and came out inverted/offset.
- Real binaural / world-locked **A/B demo must come from the operator's engine** (Windows); placeholder only otherwise.

## Pedagogy notes agreed with operator
- Stereo visual: the sound dot stays **between the ears (inside the head)** — never outside — to contrast with externalised/binaural.
- "Many HRTFs on many heads" uses the **real IRCAM LISTEN** data (individuality is strongest above ~6 kHz = pinna region).
- **Cone of confusion** is its own sequence: same ITD on a cone around the interaural axis → front/back & up/down
  ambiguous → only spectral (HRTF) cues disambiguate, and they're weaker/less reliable than timing → hardest to localise.
