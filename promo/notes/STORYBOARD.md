# Storyboard — payoff-first explainer (proposed, session 2026-10-28)

Veritasium-style: hook on the achievement, promise a crash course, teach, return to the payoff.

## Arc
1. **COLD OPEN / HOOK (0:00–~0:15)** — show the finished thing working (webcam preview + world-locked
   radar cone tracking the head). Claim the wild result:
   > "With nothing but a webcam, I got my headphones to place sound out in the room — externalised, in real 3D,
   > and *fixed in space* while I move my head. Here it is, running."
2. **CURIOSITY PIVOT (~0:15–0:30)** —
   > "But how? To get why this works, you need a bit of how we actually hear in 3D.
   > Do you know what binaural is? No? Don't move — three minutes of psychoacoustics first."
3. **CRASH COURSE (the HRTF sequences):**
   - **Stereo, in your head** (beat 1 slide + stereo L↔R audio demo).
   - **Binaural** — recreate each ear's signal; sound can sit anywhere (beat 2 orbit + audio).
   - **Timing (ITD)** — wavefront reaches the near ear first (seq1) → the measured curve, animated as the
     source sweeps azimuth, ITD readout (seq2b). Note: front AND back ≈ 0 ms → sets up the cone.
   - **Colour (spectral fingerprint / HRTF)** — spectrum morphs with direction (seq3).
   - **Cone of confusion** (seq5) — same ITD on a whole cone → only spectral cues break the tie, and they're
     weaker/less reliable → hardest to localise (front/back, up/down).
   - **Individuality** (seq4) — everyone's ears differ, so everyone's HRTF is unique (esp. >6 kHz).
4. **BACK TO THE PAYOFF:**
   - Static binaural still collapses in your head; the missing cue is **motion** (parallax "mug test", beat 3).
   - **World-lock + head-tracking** — lock the virtual speakers to the room; the webcam tracks the head so only
     the render changes (beat 3 world-lock + beat 4 webcam punch-in). *Honesty caption: externalisation is the
     hypothesis being tested.*
   - **How it's built** (beat 5): Rust engine, VB-Audio Cable in, normal Windows out, live EQ. (No latency section.)
5. **STATUS + CTA (end card, beat 6):** works on Windows today; next = per-ear HRTF fit + presets + AI judge;
   built in public, donationware; **"Comment EARS → I'll send you the build."**

## Expanded HRTF-segment VO draft (English, TTS-ready — no em-dashes, acronyms spelled)
- **ITD:** "Start with timing. A sound off to your right reaches your right ear a fraction of a millisecond before
  your left. Tiny, but your brain measures it. Here it is, measured on a real head, across every direction. Notice:
  straight in front and straight behind both give zero. Timing alone cannot tell front from back."
- **Spectral fingerprint:** "Then there is colour. Your head and the folds of your outer ears filter the sound
  differently depending on where it comes from. Each direction leaves its own tonal fingerprint. Timing plus colour,
  bundled together, is your head-related transfer function, your H R T F."
- **Cone of confusion:** "And it gets harder. Every direction on this cone shares the exact same time difference.
  Front or back, up or down, your ears cannot tell them apart by timing. Only the spectral fingerprint can break the
  tie, and that cue is weaker and less reliable. This is where your brain struggles most."
- **Individuality:** "One more catch. Everyone's ears are shaped differently, so everyone's fingerprint is unique.
  Same direction, dozens of real people, each line one person. Low down they agree. Up high, where the ear folds
  matter, they scatter. That is why one-size-fits-all spatial audio is hit or miss, and why fitting it to your own
  ears is the endgame of this project."

## Notes
- Two cuts to ship: the full explainer (~4–5 min) and a <60s teaser (hook → one demo → CTA).
- Keep the stereo/binaural audio demos; the real binaural A/B (static vs world-locked) still needs an engine render.
