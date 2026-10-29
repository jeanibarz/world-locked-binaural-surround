---
name: Promo Explainer — Hourly Improve
description: One autonomous iteration — critique the current explainer video and ship an improved version (VO fixed). Bounded to 50.
cwd: /home/jean/git/world-locked-binaural-surround-promo
checklist:
  - Analysed the current video and got fresh critic feedback (distinct lenses this iteration)
  - Applied a real batch of improvements (VO unchanged) and re-rendered explainer.mp4
  - Saved world-locked-explainer-vNN.mp4 and made a local commit + tag explainer-vNN
---

## Objective

Run **exactly one** improvement iteration on the promo explainer video, then exit. Each
iteration must make the video **more ambitious, cleaner, more professional, higher quality**
than the previous version — and ship a new numbered version. A separate hourly schedule calls
this playbook again for the next iteration, up to **50** total. Never loop inside this task.

## Fixed constraints (do NOT violate)

- **The voiceover is LOCKED.** Do not edit `promo/vo/full/vo_*.txt` or regenerate TTS
  (`vo_full/vo_*.mp3`). Improve everything else: visuals, timing, transitions, effects, colour,
  typography, new/ reworked sections, corrections, 3D. `render_all.sh` auto-retimes each visual
  to its fixed VO segment, so clips may be any length.
- Respect `promo/notes/DECISIONS.md` (externalisation = hypothesis; "built in public"; VB-Audio
  Cable; no latency section; 3-DoF; HRTF = direction not distance). Never ship a claim that breaks these.
- **Never delete** earlier `world-locked-explainer-v*.mp4` versions. Only add the new one.
- Keep it to **one coherent improvement batch** and **at most 2 critic subagents** this iteration.
- Do not block on the operator. If something can't be done safely, do a smaller safe improvement instead.

## Paths

- Canonical scripts (edit THESE, they are the source of truth, versioned per iteration):
  `promo/scripts/` → `anim.py`, `hrtf_anim.py`, `hrtf_anim2.py`, `vizchrome.py`, `mkmask.py`, `render_all.sh`
- Render workspace (persistent, outside git): `/home/jean/promo-loop/build`  (holds `vo_full/*.mp3`, `explainer-current.mp4`)
- Durable state: `promo/loop/state.json`  ·  log: `promo/loop/ITERATIONS.md`  ·  VO reference: `promo/notes/VO-SCRIPT.md`
- Output videos: `/mnt/c/Users/ibarz/Videos/world-locked-explainer-vNN.mp4`
- Python: system `python3` (Pillow/edge-tts) for `anim.py`; `/home/jean/git/binaural-localization/.venv/bin/python` for the matplotlib scripts (already wired inside `render_all.sh`).

## Steps

1. **Pick the iteration number and the stop check.**
   ```
   N=$(bash promo/scripts/loop_next.sh)
   ```
   If `N` is `DONE`: this run is past the cap. Disable the schedule and finish:
   ```
   SID=$(python3 -c 'import json;print(json.load(open("promo/loop/state.json")).get("schedule_id") or "")')
   [ -n "$SID" ] && kookr schedule disable "$SID" || true
   ```
   Report "50 iterations complete", then **complete this task** (step 9) and STOP (do nothing else).

2. **Sync canonical scripts into the build dir** (keeps `vo_full/` and `explainer-current.mp4`):
   ```
   B=/home/jean/promo-loop/build
   cp -f promo/scripts/anim.py promo/scripts/hrtf_anim.py promo/scripts/hrtf_anim2.py \
         promo/scripts/vizchrome.py promo/scripts/mkmask.py promo/scripts/render_all.sh "$B/"
   ```

3. **Analyse the current video.** Build a contact sheet of the current version so you (and the
   critics) can see the real state:
   ```
   CUR="$B/explainer-current.mp4"; [ -s "$CUR" ] || CUR=$(ls -t /mnt/c/Users/ibarz/Videos/world-locked-explainer-v*.mp4 2>/dev/null | head -1)
   # grab ~12 frames across it + tile (reuse the ffmpeg tiling pattern you know); read VO-SCRIPT.md for context.
   ```
   Read `promo/notes/VO-SCRIPT.md` and the latest few rows of `promo/loop/ITERATIONS.md` so you
   don't repeat the same change twice.

4. **Get fresh, DISTINCT critic feedback.** Choose two lenses by rotation so they differ every
   iteration. Lens list (index 0..11):
   `0 pedagogy&clarity · 1 visual design&colour · 2 typography&layout · 3 motion&transitions ·
    4 pacing&retention · 5 audio-visual sync · 6 data-viz rigour · 7 3D & new-effect opportunity ·
    8 accessibility & muted viewing · 9 storytelling & hook · 10 scientific accuracy · 11 ambition: rework a weak section`
   This iteration use lenses `A=(2*N) % 12` and `B=(2*N+1) % 12`. Spawn 2 subagents (general-purpose),
   one per lens, each given the contact sheet + the scripts + DECISIONS.md, asked for **3–6 concrete,
   ranked, implementable** improvements (Pillow / matplotlib / ffmpeg only), honouring the fixed VO and
   the caveats, and told to be bold (fill gaps, rework weak sections, add effects/3D, raise polish).

5. **Apply a real batch of improvements** by editing the canonical scripts in `promo/scripts/`.
   Aim higher than last time. Good moves: rework a weak section end-to-end, add smooth transitions
   between beats, add depth/motion/effects, improve colour/typography/composition, add a genuinely new
   visual (e.g. a 3D touch, a better hook, a cleaner diagram), fix anything inaccurate. Re-sync the
   edited scripts to `$B` (`cp` as in step 2).

6. **Render** the new version:
   ```
   bash promo/scripts/render_all.sh "$B"
   ```
   Verify `$B/explainer.mp4` exists and its duration is plausible (> 180 s). If the render fails,
   fix your edit and re-render; do not ship a broken video.

7. **Ship + record** (mechanical, do not hand-roll): 
   ```
   bash promo/scripts/iterate_finish.sh "$N" "<one-line summary of this version's improvements>"
   ```
   This copies the video to `world-locked-explainer-v$N.mp4`, updates `state.json` + `ITERATIONS.md`,
   and makes a **local commit + tag `explainer-vNN`** (and best-effort push).

8. **Report** briefly: version N shipped, the two critic lenses used, the concrete improvements made,
   and the output path.

9. **Complete the task (REQUIRED — do not just stop).** This is a *scheduled, one-shot* task: the
   next iteration is a **separate** hourly run. If you only stop emitting output, this task lingers in
   `inProgress` and the schedule gate skips every hourly fire until the 3h hung-task reaper kills it —
   silently losing ~3 iterations each time. So end the turn by marking the task terminal:
   ```
   kookr signal completion-ready --note "explainer v$N shipped; one iteration done" || true
   curl -sS -X POST "${KOOKR_API_BASE_URL:-http://127.0.0.1:4800}/api/tasks/${KOOKR_TASK_ID}/complete" \
     -H 'content-type: application/json' -d '{}' >/dev/null 2>&1 || true
   ```
   Then STOP. (The same applies to the `DONE` / cap-reached path in step 1 and to any early safe-exit.)

## Idempotency

- `loop_next.sh` derives N from `state.json`; `iterate_finish.sh` is what advances it. If a prior run
  crashed before step 7, N is unchanged and re-running safely overwrites `v$N`. If `v$N.mp4` already
  exists and `state.json` already shows `iteration == N`, the iteration is done — do nothing and exit.
- Never run `render_all.sh` more than needed; one good render per iteration.

## Anti-patterns

- Editing or regenerating the VO. Deleting old versions. Looping inside this task. Spawning more than
  two critics. Shipping a broken or unchanged video. Making a claim that breaks DECISIONS.md. Blocking
  on the operator. Rewriting the whole video from scratch every time (improve the current one).
