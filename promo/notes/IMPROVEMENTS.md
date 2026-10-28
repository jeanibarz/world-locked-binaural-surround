# Improvement backlog — HRTF crash course (from a 4-specialist review)

Synthesis of four parallel reviews (pedagogy, data-viz, motion/visual, psychoacoustics accuracy)
of the rendered sequences. Deduplicated, prioritized. Lens tags: [PED] [VIZ] [MOT] [SCI].

> This is a "good basis" — these are **next-iteration** improvements, not blockers. Respect the
> locked accuracy caveats in `DECISIONS.md` throughout.

## P0 — highest leverage (do first)

1. **Cone tie-breaker: fix the science AND bridge to the payoff.** [SCI][PED]
   Current "only the spectral fingerprint can break the tie" is incomplete. On the cone, BOTH
   interaural cues (ITD **and** ILD/loudness) are ~constant. A *stationary* head leans on the
   fragile spectral cue; the dominant real-world front/back resolver is **head motion** (Wallach):
   turning the head shifts the timing in **opposite** senses for a front vs a back source.
   → Rewrite: "Every direction on this cone shares the same timing **and the same loudness**. A
   still head can only lean on the spectral fingerprint — fragile. But **turn your head**, and the
   timing shifts one way for a sound in front, the other way for one behind. Hold that thought."
   This is the keystone: it's correct, and it *is* the bridge from the crash course to world-lock.

2. **Build the mechanism instead of asserting the stereo→binaural jump.** [PED]
   Add the keystone idea: your brain locates by **comparing the two ears**; stereo only changes
   **loudness** — a combination that never occurs with a real sound — so the brain gives up and
   plants it in your head. Binaural restores the real differences → it jumps out. Make step 2 **pose
   the question** ("what differences? — the next three minutes") instead of pre-answering it.

3. **Reframe "spectral weaker/less reliable than timing."** [SCI]
   Apples-to-oranges (on the cone, timing gives *zero* info — different job). Replace with: spectral
   cues are **fragile — high-frequency, need a broadband sound, and listener-specific.** Keep "this is
   where localisation is hardest" (true: reversals + elevation errors are the biggest localization errors).

4. **Unify the chrome so the plots stop looking like a different project.** [MOT][VIZ]
   Render each matplotlib figure with **transparent background** onto the **same Pillow canvas**
   (dot-grid + vignette), then draw the **section tag** (top-left `NN`) and the **lower-third caption
   bar** (boxed, cyan tick) with Pillow — identical to the slides. Enforce the house rcParams inside
   plots (mono font, muted ticks, grid `#1e2a33`, spines off, hero lines ≥2.5–3px).

5. **Connective tissue: every transition is a question the next step answers + a recap.** [PED]
   - End the ITD step on a full-screen cliffhanger: **"So how do you *ever* tell front from back?"** →
     answered by the cone. (Today it's small grey text.)
   - Pose step 7 before the plot: "Same direction — would two people hear the same thing?"
   - Add a ~10 s **recap** before returning to the payoff (currently none): timing = left/right;
     colour = front/back + up/down; colour is personal + imperfect → "what headphones must fake."

## P1 — strong, mostly cheap

6. **Amber discipline.** [MOT][VIZ] Reserve amber `#f5b556` for ONE role (marker / warning / the
   ambiguous thing). It is currently also a *data series* on the 5-direction spectra → recolor those
   with an **ordered sequential ramp** (front→behind), never 5 random hues. Rule: cyan = signal/true
   cue; amber = problem/ambiguity; ink = headings; muted = labels/axes only. Put it as a code comment.

7. **Individuality: median + percentile band, not spaghetti.** [VIZ][PED][SCI]
   Replace the 28–30-line overlay with a **median line + shaded 10–90 band** (optionally faint ghost
   lines behind). The band **fans open >6 kHz** = the whole point, in one glance. Overplot ONE amber
   "you" line. Shade the >6 kHz region with two tags: "below 6 kHz — everyone agrees / above — your ears
   are unique." Close the pinna loop verbally (pinna folds → notches → notches differ → fit to *your* ears).

8. **ITD curve: make vertical position = the answer + de-jargon + mark the zeros.** [VIZ][SCI][PED]
   - **Flip the y-sign** so right-side sources go **up** (matches "right = up" intuition), OR at minimum
     label the y-axis halves: **"upper = left ear first / lower = right ear first"** (resolves the
     signed-curve vs magnitude-readout snag).
   - Readout in plain words **next to the marker**: "right ear hears it ~0.5 ms sooner" (one decimal, drop
     the raw "ITD" acronym in-frame).
   - Put labelled dots at the **0° and ±180° zero-crossings** ("front: 0 ms" / "behind: 0 ms, same!").
   - Keep the smoothing (already done); don't let anyone read exact peak numbers (single-subject artifact).

9. **De-jargon the spectra axis.** [VIZ][PED] "right-ear gain (dB)" → "how loud each pitch arrives /
   tone colour (dB)", dashed **0-dB reference** labelled "unchanged", axis ends "+ boosted / − muffled".
   Log-x explicit **kHz ticks** (0.2, 0.5, 1, 2, 5, 10, 20 → "1k, 2k, 5k…"). Tighten y to ~−40…+8.

10. **Spectral step: concrete → general.** [PED][SCI] Start from ONE deep notch + its mechanism
    ("that dip is a frequency your ear-fold cancels, and which frequency depends on the angle"), THEN
    widen. Show **3–5 directions at once** (the `plots_sheet` panel is ahead of the lone morph — promote it).
    Optional strong upgrade: pair **"right 45" vs "behind-right"** (a near cone pair): "same timing,
    different colour — that's how you tell them apart" = a direct proof of the spectral tie-breaker.

11. **Name ILD (the level difference).** [SCI] The step-2 L/R meters already show it — name it once
    ("the near ear is also **louder**"), and in the cone beat say "both the timing and the loudness match
    across this cone" (closes the "just use the louder ear" logical hole).

12. **Add the wow-number for ITD.** [PED] Brain resolves interaural timing to ~**10 microseconds**
    (~1° azimuth) — distinct from the max ITD (~0.6 ms). Big hook for the SWE audience.

13. **Define HRTF exactly once**, at the END of the spectral step (remove the "= HRTF" label from the
    binaural/orbit slide, which seeds a wrong model). [PED][SCI]

## P2 — polish / production value

14. **Redo the "mug/parallax" slide (beat 3).** [MOT] Reuse the top-down head asset; a fixed object stays
    pinned (cyan) while a second copy rotates with the head (amber); kill the dead void; center it.
15. **Cone legibility.** [VIZ][MOT][PED] Lock a fixed ¾ camera (never edge-on = looks broken), fill the
    cone translucent amber (surface, not ring), demote the sphere to a faint silhouette; OR a **2D side
    profile** (head in profile + fan of "0 ms" arrows + 2–3 candidate dots) — more legible at phone size.
    Prefer the 2-state (tilted→vertical) read over a static ball.
16. **Motion verbs + smoothstep easing.** [MOT] Assign each element a verb (draw-on synced to the sweep,
    orbit travels, wavefront pulse on the near ear, slow cone rotation). Drive positions with
    `t*t*(3-2t)`, never linear. Kill "everything fades in" sameness.
17. **Connective transitions / match-cuts.** [MOT] Carry a motif across cuts (the cyan source dot flies
    from the orbit onto the ITD plot); 3-frame cyan radial wipe + a soft whoosh where a hard cut is needed.
18. **Depth cues + typography.** [MOT][VIZ] Glow behind focal objects, 1px inner highlight + soft shadow on
    panels, dim inactive signal-flow boxes to ~55%. Axis labels in **ink** (load-bearing), tick numbers muted,
    ≥22–26px at render scale. Real glyph `→` everywhere (not ASCII `->`). Promote the plain-language
    `plots_sheet` titles into the animated slides.
19. **Consistent HUD elements + webcam chrome.** [MOT] Decide a rule for the "headphones on" chip and the
    L/R meters (persist through the headphone section or drop after establishing). Give the webcam punch-in
    the section tag + caption + a thin cyan inner frame/corner ticks; tint its blurred bg toward `#0a0e14`.
20. **End card: one live motion** (breathing rings / CTA glow sweep / slow compass spin) so it doesn't dead-hold.

## Must-fix factual defects
- **Subject count:** crash-course says "30 people", plots say "28" → reconcile to the real number used. [SCI]
- **Step-2 overclaim:** "Now it can come from anywhere around you" → "**can be placed** anywhere" (static
  binaural often still in-heads; don't contradict the later payoff / the externalisation-is-hypothesis caveat). [SCI]

## Validated — do NOT break (reviewers confirmed)
- ITD sign/"which ear leads" convention is internally consistent and correct (verified across frames). [SCI]
- "Front and back ≈ 0 ms" is correct and the right seed for the cone. [SCI]
- Cone geometry is correct, incl. the degenerate limit (near-0 ITD → flattens to the median plane). [SCI]
- HRTF = single bundling filter, direction not distance. [SCI]
- Individuality concentrated >5–6 kHz (pinna). [SCI]
- Step 3→4 (wavefront → full curve) is a textbook concrete→general build. [PED]
- Real IRCAM LISTEN data is credible and on-brand. [PED][SCI]
