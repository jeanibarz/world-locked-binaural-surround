# Document under review — promo video VO script + LinkedIn article

## SHARED DOCUMENT PACK (orientation — verify against the text, do not treat as settled)

**Thesis.** Headphone "surround" fails because the sound stage is locked to your head and turns with it, so it never feels like it's out in the room. This project keeps a virtual 7.1 speaker set *world-locked* (fixed to the room) and uses a webcam to track the head so only the render changes — aiming to restore the motion cue the brain uses to externalise sound. It runs today on Windows (Rust DSP, virtual audio device, live EQ, cockpit UI); latency has been cut hard; externalisation benefit is still a hypothesis being tested.

**Audience.** LinkedIn. Technically literate readers (many software engineers), but NOT audio/DSP/psychoacoustics experts. Watching a video (often muted, scrolling) and/or reading a long-form article. Must understand AND believe it, and want to follow/test.

**Register.** First-person, indie creator building in public. Confident, honest, not boastful. The video VO is read by a neural TTS voice (so: short sentences, no em-dashes/semicolons, acronyms spelled out, numbers as words, one idea per sentence, speakable rhythm).

**LOAD-BEARING CLAIMS & SCOPE TAGS (must survive any rewrite — do NOT strengthen or drop):**
- Externalisation / "trains your brain / better front-back" = **HYPOTHESIS the project is built to test**, not a measured result. No human A/B test exists yet. Must never read as proven. Keep "the bet is…/the hypothesis…" strength.
- Latency: the ~90 ms → ~20 ms win came from **moving off the old MME driver onto WASAPI (shared)** — NOT exclusive mode (which was reverted). Keep attribution exact.
- "~40 ms" = the **felt-latency floor / output buffer floor**, NOT the measured pose-age (which is ~80 ms p50). Prediction covers the camera lag perceptually; closing the gap is **work in progress**. Do NOT write "below perception" as a fact; the 50–75 ms threshold is unverified literature.
- "Built **in public**" — NOT open-source (repo is all-rights-reserved). Never write "in the open" / "open source".
- Head-tracking reliable to **~±45°** (loses face past ~±50°). Rust kernel ~5× real-time on CPU, GPU optional. Virtual device works today via a third-party virtual sound card. Smyth Realiser comparison is an **aspiration/goal**, not achieved parity.
- HRTF is the single filter that already bundles the time/level/spectral cues — not a third cue parallel to ITD/ILD.

---

## PART A — VIDEO VOICEOVER SCRIPT (what the TTS voice says; ~3:40, 7 beats)

**Beat 1 — Hook (0:00–0:20).**
"Your headphones lie about where sound comes from. In a game, the footsteps behind you should stay behind you when you turn to look. On headphones, they swing around with your head. The whole room is glued to your skull. I'm fixing that — with nothing but your webcam."

**Beat 2 — The idea, world-locking (0:20–0:52).**
"Here's the idea. Instead of gluing the sound to your head, this app locks a full set of virtual speakers to the world. They stay fixed to your screen and your room, like real speakers on real stands. Turn your head, and the speakers don't move — only the calculation that turns them into headphone sound changes. The gunfire stays where it happened. Your ears moved, not the room."

**Beat 3 — Why it helps / how two ears locate sound (0:52–1:32).**
"Why does that help? In the real world you locate sound with two ears. A sound reaches one ear a fraction earlier, and a little louder. Your head and outer ears also add a tonal fingerprint that depends on the direction. Engineers capture all of it in one filter — your head-related transfer function, or H R T F. Recreate it over headphones and sound can sit to your left, your right, even above you. But play it back fixed, and it still feels like it's inside your head. The cue that most convinces your brain a sound is truly out there is missing — and that cue is movement."

**Beat 4 — The webcam head-tracker (1:32–2:04).**
"That's where the webcam comes in. A face-tracking model reads your head — how far you turn, tilt, and nod — straight from the video, accurate across the roughly forty-five degrees you use glancing between screens. Many times a second, the engine works out where each virtual speaker sits relative to your head, and applies the matching ear filter for that angle. Turn your head, and the sound follows, block by block."

**Beat 5 — Under the hood (2:04–2:38).**
"Under the hood, the core is written in Rust. It runs the audio math on an ordinary processor, about five times faster than it needs to — no graphics card required. It shows up in Windows as a virtual surround sound card, so any app can play into it. On the way out, it applies a correction for your headphones and a live equalizer you can adjust. This cockpit shows all of it live: your head pose, the world-locked speaker radar, output levels, and latency."

**Beat 6 — The hard part, latency (2:38–3:15).**
"The hardest part is latency. The sound has to match where your head is when you actually hear it — not where the camera last saw it. The biggest win was the output path: moving off an old Windows audio driver cut the output buffer from about ninety milliseconds to about twenty. Then the software predicts where your head is heading, to cover the camera's delay. That pulls the felt lag toward a floor of around forty milliseconds. Closing the last gap, so no one could notice, is what I'm working on now."

**Beat 7 — Status, vision & CTA (3:15–3:52).**
"Where is it today? It works end to end on Windows: a world-locked seven-point-one stage, camera tracking, live EQ, all in one cockpit. Next up — tuning the sound filters to your own ears, saved presets, and an AI judge that scores your setup objectively, so you're not trusting your ears alone. The goal: the out-in-the-room feel of a multi-thousand-dollar hardware box, in software, on the headphones you already own. It's being built in public, block by block. If you game or mix on headphones, I want your ears — comment the word EARS and I'll send you the build."

---

## PART B — LINKEDIN ARTICLE (~950 words; stands alone; embeds the video)

**Headline:** I'm Building a $4,000 Surround-Sound Experience in Software — Using Your Webcam

Put on headphones, start a film mixed in surround, and turn your head. The whole sound stage turns with you. The explosion that was on your left is now wherever you're facing. Real speakers don't do that — they stay in the room while you move. That gap is why "surround" on headphones so often feels like it's trapped inside your skull. I've been building a renderer that closes it, and it needs no special hardware — just your webcam.

[ Embedded video — ~3:40 explainer. Here's the three-minute version. Below, I go deeper than the video does. ]

**The problem: your sound stage is glued to your skull.**
Normal headphone surround is head-locked: the virtual speakers are fixed relative to you, so when you turn, they turn too. Your brain gets a contradiction — the picture says "turn," the sound says "nothing moved" — and resolves it by deciding the sound was never out in the room to begin with. In a shooter, footsteps behind you should stay behind you when you glance at a corner. Head-locked audio swings them around with your view. That's the difference between reacting to a threat and spinning at a ghost.

**The fix: lock the speakers to the world, not your head.**
This project inverts the usual design. It places a full virtual 7.1 set world-locked — fixed to your screen and your room, like speakers on real stands. When you turn your head, the speakers don't move; only the calculation that renders them to your two ears changes. The gunfire stays where it happened, and your ears are what moved, not the room.

**How your brain locates sound (the 90-second version).**
You locate sound with two ears. A source reaches one ear slightly earlier and slightly louder, and your head and outer ears stamp a direction-dependent "tonal fingerprint" onto it. Audio engineers bundle all of that into one filter — your Head-Related Transfer Function (HRTF). Recreate it over headphones and sound can sit left, right, or overhead. But static binaural still tends to collapse inside your head, because the cue that most convinces the brain a sound is external is missing: motion. Move your head a few degrees and a real source shifts in a specific, consistent way. Give the brain that, and it commits. That's the cue world-locking restores — and it's the hypothesis this whole project is built to test, not yet a measured result.

**No special hardware — just your webcam.**
To keep the scene fixed while you move, the renderer needs your head orientation in real time. A face-landmark model reads yaw, pitch and roll from the webcam, reliably across the ~±45° you actually use glancing between screens. Many times a second, the engine computes each virtual speaker's direction relative to your current pose and convolves it with the matching impulse response for that angle. Turn your head; the render follows, block by block. Using a commodity webcam as a 6-ish-DoF head tracker — instead of a $200 IMU headset — is the part I'm most happy with.

**The hard part: latency (the engineering deep-dive).**
The illusion only holds if the sound matches your head at the instant you hear it, not when the camera took the frame. So the metric that matters isn't frame rate — it's the age of the head pose by the time the audio reaches your ears. Measuring that as a distribution (not a mean) is what exposed the real bottleneck: the audio output path. The engine was defaulting to an old Windows audio driver (MME); moving output onto WASAPI dropped the device buffer from ~90 ms to ~22 ms. (Exclusive mode went lower still, ~13 ms, but I reverted it — it broke the resampling between VoiceMeeter and the DAC and caused dropouts.) On top of that, an α-β predictor extrapolates each head angle forward to cover the camera's remaining lag, so the binaural cue lines up with where your head will be. The felt latency now sits near a floor of ~40 ms. Getting the measured pose-age distribution comfortably under the ~50–75 ms range where listeners start to notice is the current frontier — and yes, I need to validate that threshold by ear, not just cite it.

**What works today — and what's next.**
Running end-to-end on Windows right now: a world-locked 7.1 stage, webcam head-tracking, a native Rust DSP kernel (~5× real-time on CPU, no GPU), live parametric EQ and a personal headphone correction, all driven from one cockpit. Next: fine-tuning the HRIR toward your own ears, presets, and an AI "judge" (a synthetic listener) that scores a calibration objectively instead of trusting an unreliable ear. The aspiration is the out-in-the-room externalisation of a multi-thousand-dollar hardware rig like the Smyth Realiser — in software, on the headphones you already own. I'm being honest that the externalisation benefit is still a hypothesis I'm measuring, not a shipped claim.

**Built in public — and I want your ears.**
It's being built in public, block by block, with a living research journal that records every dead end and negative result. It'll be donationware. If you game or mix on headphones, I'd love your ears on it before the next release: comment "EARS" and I'll send you the build and a two-minute calibration — your feedback literally picks the next block I build. And tell me honestly: does surround sound living "inside your head" bother you as much as it bothers me?

#SpatialAudio #AudioDSP #Rust #GameAudio #BuildInPublic
