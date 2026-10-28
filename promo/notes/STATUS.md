# Status & next steps

_Last updated: session 2026-10-28._

## Done
- **Plan / script / montage / LinkedIn article** — in `plan/promo-plan.html` (+ claude.ai artifact).
  Fact-checked (DSP audit) and rewritten after a 6-lens communication review. Latency section removed.
- **Styled 6-beat cut** — `C:\Users\ibarz\Videos\world-locked-cut2.mp4` (~2:26). Beats:
  1 stereo-in-head (+ stereo audio demo) · 2 binaural orbit (+ orbit audio) · 3 catch/parallax/world-lock ·
  4 webcam punch-in (blurred bg) · 5 signal-flow · 6 end card.
- **HRTF crash-course sequences** (standalone clips, silent, in Windows `Videos\`):
  - `world-locked-seq1_itd_wavefront.mp4` (11s, Pillow wavefront → near ear first)
  - `world-locked-seq2_itd_curve.mp4` (OLD static real ITD curve — operator disliked; superseded)
  - `world-locked-seq2b_itd_sweep.mp4` (REWORK: source sweeps azimuth, dot slides on smoothed curve, ITD readout)
  - `world-locked-seq3_spectra_morph.mp4` (10s, HRTF spectrum morphs with direction + inset head)
  - `world-locked-seq4_individuality.mp4` (8s, 30 real people, pinna region highlighted)
  - `world-locked-seq5_cone_of_confusion.mp4` (3D, same ITD on a cone → spectral cues needed)
- **Real-data stills** — `scripts/hrtf_plots.py` → plot_itd / plot_spectra_dir / plot_individuality.
- **Preservation** — this worktree.

## Build-script → output map
| script | produces |
|---|---|
| `tts_gen.py` | `vo1..6.mp3` (TTS) |
| `anim.py <beat> <dir> <dur>` | Pillow frame sequences (beats + `itd`) |
| `build_full.sh` | the full styled cut → `world-locked-cut2.mp4` |
| `build_poc*.sh` | earlier 2-beat PoCs (poc4 = blur + corrected pan) |
| `build_video.sh` | the FIRST rough cut (raw full-screen UI, superseded) |
| `hrtf_plots.py` | real-data stills |
| `hrtf_anim.py` + `build_seq.sh` | seq1–seq4 |
| `hrtf_anim2.py` + `build_seq2.sh` | seq2b (ITD sweep) + seq5 (cone) |
| `mkmask.py` | `mask_face.png` for the webcam portrait blur |

## Next steps (proposed)
1. **Adopt the payoff-first structure** — see `STORYBOARD.md`. Rewrite/extend the VO accordingly
   (new cold-open + "don't move, psychoacoustics first" pivot; the HRTF sequences become the crash course).
2. **Generate the expanded HRTF-segment VO** (draft in STORYBOARD) and time the sequences to it.
3. **Final assembly** of the full explainer (beats + HRTF sequences + transitions + music bed).
   Add: cold-open 2s, "🎧 best with headphones" card, transition whooshes, light music, end-card CTA hold.
4. **Operator to record the real binaural A/B** (static vs world-locked) on Windows → drop into beat 3 / payoff.
5. Optional: **3D (three.js headless)** variant of the ITD wavefront on a real head.
6. Make a short **teaser** cut for the feed.
7. Housekeeping: parameterize the hardcoded scratchpad paths in the scripts.

## Open questions for the operator
- Keep seq2 (old static ITD curve) deleted in favour of seq2b? (operator leaned delete/rework — reworked.)
- Webcam: keep portrait-blur, or re-record with a neutral background?
- Final length target for the explainer, and do we also ship a <60s teaser?
