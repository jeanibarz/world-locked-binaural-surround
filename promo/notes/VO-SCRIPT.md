# Voiceover script — payoff-first explainer (full)

English, neural TTS (edge-tts `en-US-AndrewNeural`, rate −4%). TTS-safe: no em dashes,
numbers as words, acronyms spelled ("H R T F", "A I"). ~854 words, **~4:53** of audio.
Segments live in `promo/vo/full/vo_*.txt`; regenerate audio with `promo/vo/full/tts_full.py`.
Respects all `DECISIONS.md` caveats (externalisation = hypothesis; built in public; VB-Audio
Cable; no latency section; 3-DoF; HRTF = direction not distance).

Asset mapping + measured per-segment durations (for retiming clips at assembly):

| # | segment | dur | visual |
|---|---|---|---|
| 01 | cold / hook | 19.5s | cockpit + webcam/radar (the working result) |
| 02 | pivot "don't move" | 7.9s | transition card |
| 03 | stereo | 24.2s | b1 stereo |
| 04 | binaural | 19.9s | b2 binaural |
| 05 | timing (ITD) | 36.0s | s1 wavefront → s2 ITD sweep |
| 06 | tone colour (HRTF) | 24.8s | s3 spectra morph |
| 07 | cone of confusion | 35.5s | s5 cone |
| 08 | individuality | 24.7s | s4 individuality |
| 09 | missing cue / world-lock | 40.9s | b3 (parallax + world-lock) |
| 10 | head tracker | 11.1s | b4 webcam punch-in |
| 11 | under the hood | 22.9s | b5 signal flow |
| 12 | status + CTA | 26.1s | b6 end card |

> Length note: ~4:53 is a full explainer. Trim levers: segments 05 timing, 07 cone, 09 missing-cue
> are the longest. Also planned: a <60s teaser cut (hook → one demo → CTA).

---

## 01 — cold open / hook
Put on headphones. Right now, every sound you hear is trapped inside your head. But watch this. With a webcam and some code, I got these same headphones to place sound out in the room, in real three D, and hold it fixed in space while I move my head. No special hardware. Just the camera, watching me.

## 02 — pivot
How does that work? To really get it, you need a quick tour of how you actually hear in three D. Give me three minutes. Don't move.

## 03 — stereo
Start with the problem. On plain headphones, a sound can only slide from your left ear to your right. A thin line, between your ears, inside your head. Your brain finds direction by comparing your two ears, and plain stereo only changes how loud each side is. That is a pattern that could never happen with a real sound in a real room. So your brain gives up, and leaves it in your head.

## 04 — binaural
To escape your head, you have to give each ear exactly what it would really hear. Do that, and a sound can sit anywhere around you. In front, behind, even above. This is what spatial audio is built on. But what, exactly, is different between your two ears? Two things. Let's take them one at a time.

## 05 — timing (ITD)
First, timing. A sound on your right reaches your right ear a hair before your left. The gap is tiny, well under a thousandth of a second. But here is the astonishing part. Your brain resolves that gap down to about ten millionths of a second. That is a precision of a few degrees, from just two ears. Measured on a real head, across every direction, it looks like this. The further a sound swings to one side, the bigger the head start. But notice: straight in front, and straight behind, both give zero. So timing alone can never tell front from back.

## 06 — tone colour (HRTF)
That is where the second clue comes in. Colour. Your head, and the folds of your outer ears, filter a sound differently depending on where it comes from. Every direction leaves its own tonal fingerprint, with dips and peaks your brain has learned to read. Timing, plus this colouring, bundled into one filter, is what engineers call your head-related transfer function. Your H R T F.

## 07 — cone of confusion
But that colour is a fragile clue, and here is where it bites. Pick any timing. It is never just one direction. It is a whole ring of them. Sweep that ring, and you get a cone. Every direction on this cone reaches your ears with the same timing, and the same loudness. Front or back, up or down, your ears cannot tell them apart. A still head has only that faint colour to go on. But watch what your brain really does. Turn your head. The timing shifts one way for a sound in front, and the other way for a sound behind. Movement breaks the tie. Hold that thought.

## 08 — individuality
One last catch. Everyone's ears are shaped differently. So everyone's fingerprint is unique. Here are dozens of real people, the same direction, each line one person. Down low they roughly agree. Up high, where the folds of the ear matter most, they scatter. That is why generic spatial audio is hit or miss, and why fitting it to your own ears is the endgame of this project.

## 09 — missing cue / world-lock
So, back to headphones. Even good binaural, held still, slides back into your head. The missing ingredient is the cue your brain trusts most. Movement. In a real room, turn your head and the speakers stay put, and your brain uses that to lock the sound in place. Normal headphones do the opposite. Turn your head, and the whole scene turns with you. So this project flips it. It locks a full set of virtual speakers to the room, and tracks your head with your webcam. Turn your head now, and the speakers don't move. Only the render changes. Does that finally pull the sound out of your head for good? That is the bet, and I am testing it with real listeners.

## 10 — head tracker
The tracker is just a face model on your webcam. No special hardware. Many times a second, it finds where each speaker sits relative to your head, and re-renders to match.

## 11 — under the hood
Under the hood, the heavy lifting is a Rust engine, running the audio on a normal processor, about five times faster than real time, no graphics card needed. Any app plays into a virtual audio cable, the engine renders it head-tracked, and sends it to your headphones through Windows. Along the way, it corrects for your headphones and runs a live equalizer.

## 12 — status + CTA
Where is it today? It works end to end on Windows. Next: tuning those ear filters to your own ears, presets, and an A I judge that scores your setup the same way every time. The goal is the out-in-the-room feel of a multi-thousand-dollar rig, in software, on headphones you already own. It is being built in public. If you game or mix on headphones, comment the word EARS, and I'll send you the build.
