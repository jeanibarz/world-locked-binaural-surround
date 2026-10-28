# Promo video — script v1 (English, deep dive, ~3:30–4:00, neural TTS VO)

Project: **World-Locked Binaural Surround** — a real-time, head-tracked binaural renderer for Windows.
Audience: LinkedIn — technically literate (many software engineers) but NOT audio/DSP experts.
Goal: make them *understand* and *believe* the idea, and want to follow the project.

Format of each beat: [TIME] VO (voiceover) | ON-SCREEN (text/graphic) | B-ROLL (source footage or asset).
Source footage is one continuous cockpit screen-capture (527 s); key asset = head turning → radar cone follows.

---

### 1. HOOK (0:00–0:20)
VO: "Put on headphones, start a game or a film in 'surround', and turn your head. Notice something? The whole sound stage turns with you. The explosion that was 'to your left' is now wherever you're looking. Your brain isn't fooled — the sound stays stuck inside your head."
ON-SCREEN: "Headphone surround has a problem."
B-ROLL: cockpit, then a quick illustration of soundstage rotating with the head.

### 2. WHAT YOUR BRAIN ACTUALLY DOES (0:20–0:55)
VO: "In the real world you locate sound without thinking. Your two ears receive slightly different signals — a tiny time difference, a level difference, and a spectral 'fingerprint' your head and outer ears stamp onto every sound depending on where it comes from. That fingerprint is your personal Head-Related Transfer Function. Recreate those two ear-signals over headphones and you get *binaural* audio — sound that can sit to your left, your right, above you."
ON-SCREEN: "ITD · ILD · HRTF — how two ears locate sound"
B-ROLL: simple diagram of head + two ears + a source; the two cues.

### 3. WHY STATIC BINAURAL ISN'T ENOUGH (0:55–1:20)
VO: "But record it once and play it back fixed, and most people still hear it *inside* their head, not out in the room. The brain's single strongest cue is missing: motion. In real listening, when you move your head a little, a real source shifts in your ears in a very specific way — and that tiny, consistent change is what convinces your brain the sound is truly out there."
ON-SCREEN: "The missing cue: head motion"
B-ROLL: head turning slightly; arrows showing source staying fixed while ears move.

### 4. THE IDEA — WORLD-LOCKING (1:20–1:55)
VO: "So this project flips the usual design around. Instead of a sound stage glued to your head, it places a full virtual 7.1 speaker set *locked to the world* — fixed relative to your screen and your room. When you turn your head, the speakers don't move. Only the math that renders them to your ears changes. Keep that up and your brain starts to externalise the sound — placing those virtual speakers in real space, with better front-versus-back and up-versus-down."
ON-SCREEN: "World-locked: the room stays still, you move."
B-ROLL: cockpit radar — the "WORLD-LOCKED RING" — head turns, cone swings, speaker dots stay fixed. **money shot.**

### 5. WHY HEAD-TRACKING, AND HOW (1:55–2:30)
VO: "To keep the scene fixed while your head moves, the renderer has to know, continuously, where your head is pointing. No special hardware — just your webcam. A face-landmark model estimates your head's yaw, pitch and roll in real time, reliably across the range you actually move while seated. Every audio block, the engine recomputes each speaker's direction *relative to your current head pose* and convolves it with the matching impulse response for that direction. Turn your head; the convolution follows within milliseconds."
ON-SCREEN: "Your webcam → head pose → per-block re-render"
B-ROLL: webcam preview with face mesh; head-pose numbers (yaw/pitch/roll) updating; radar reacting.

### 6. UNDER THE HOOD (2:30–3:00)
VO: "The heavy lifting runs in a native Rust kernel, doing the convolution in real time on the CPU — several times faster than real time, no GPU required. It captures any Windows app's surround output through a virtual audio device, renders it head-tracked to any headphones, and applies a personal headphone correction and live parametric EQ on the way out. This cockpit shows it all live: head pose, the world-locked speaker radar, output levels, and latency."
ON-SCREEN: "Rust DSP · virtual 7.1 device · live EQ · any headphones"
B-ROLL: cockpit SYSTEM panel, EQ, output meter, mode toggles.

### 7. LATENCY — THE HARD PART (3:00–3:25)
VO: "The illusion only holds if the sound matches your head at the instant you *hear* it — not when the camera saw it. So the metric that matters is the age of the head pose by the time the audio reaches your ears. Moving the output path to exclusive-mode WASAPI cut the device buffer from about ninety milliseconds to twenty, and a prediction step extrapolates your head orientation forward to cover the camera's lag — landing the end-to-end delay under the threshold where the brain would notice."
ON-SCREEN: "Pose age at the ear: ~40 ms — below perception"
B-ROLL: cockpit LATENCY panel, histogram, the latency number.

### 8. STATUS + VISION + CTA (3:25–3:55)
VO: "Today it works end to end on Windows: world-locked 7.1, camera tracking, live EQ, all in one cockpit. Next up — fine-tuning the HRIR to your own ears, presets, and an AI 'judge' that scores your calibration objectively instead of by unreliable ear. The goal: the externalisation of a multi-thousand-dollar hardware rig, in software, on the headphones you already own. It's being built in the open, block by block. Follow along — and tell me what your ears hear."
ON-SCREEN: "Working on Windows today · built in the open · [follow / link]"
B-ROLL: cockpit wide; end card with project name + icon + link.

---

## Accuracy anchors (for the critic agents to check against the repo)
- Virtual speakers world-locked to screen/room; head rotation changes convolution, not the scene. (README)
- HRIR/BRIR convolution per virtual speaker, interpolated between measured directions; SOFA files; owner's measured BRIRs. (requirements FR-5/FR-7)
- Head tracker: webcam + MediaPipe FaceLandmarker, yaw/pitch/roll, reliable ~±45–50°. (requirements FR-4, journal 05)
- Native Rust render kernel, ~5× realtime on CPU, GPU optional. (README status, M8)
- Virtual Windows audio device captures any app's 7.1; output to any headphones; headphone comp + parametric EQ. (FR-1/2/6/8)
- Latency: pose-age-at-the-ear metric; MME→WASAPI + exclusive mode took device buffer ~90→~22 ms; orientation prediction (quaternion extrapolation + α-β); ~40 ms floor; perceptual threshold ≈ 50–75 ms. (README, journal 36/40/42/43/46/47)
- Status: end-to-end on Windows; world-locked 7.1 passthrough validated live; EQ live; cockpit. Open: HRIR fine-tune, presets, AI-judge. (README, journal status table)
- Comparison: Smyth Realiser A16 = dedicated hardware, thousands of $. Donationware. (README)

## Known open questions to flag in the artifact
- Is "trains your brain / over time" overclaimed vs. evidence? (perceptual hypothesis, not yet a measured result)
- Exact latency numbers to show on screen — confirm against latest journal.
- Music bed / captions styling; TTS voice choice (en-US-* neural).
