# 48 — High-fidelity TrueType type in the host console (B6.9)

**Date:** 2026-10-19  ·  **Milestone:** M6 (GUI)  ·  **Status:** done; both host GUIs render crisp
TrueType type, verified by host PNG renders; 351 tests, ruff clean

## Question

The owner's verdict on the GUI: *"font looks like low resolution, small; the design is not truly
professional / high quality."* What makes it look that way, and can it match the polished mockup
(`scratchpad/calib_gui_mockup.html`) without redrawing the layout?

## Context

Both host GUIs (`calibration_gui.py`, `voicemeeter_passthrough.py`) draw through one shared
renderer, `apps/_console.py`, and **every glyph went through `cv2.putText`**. OpenCV ships only
**Hershey fonts** — single-stroke vector outlines from the 1960s: thin, no real weights, no
hinting, jagged even with `LINE_AA`, and small at the scales we used (0.34–0.62). Journal 30 said
as much when B6.3 matched the mockup's *layout and palette* but not its type: *"cv2 has only
Hershey fonts, so the type differs from the HTML mock."* The mockup itself names three real faces —
**Rajdhani** (display), **IBM Plex Sans** (body), **IBM Plex Mono** (readouts/HUD). So the gap was
never layout; it was the glyph rasteriser. The design direction was already approved — this is a
**fidelity** fix, not a redesign, so no new mockup round was needed.

## Hypothesis

Rasterising the *same* layout with hinted, anti-aliased **TrueType** glyphs (the mockup's own
faces) — and a small global size bump — removes all three complaints at once, with no layout change,
because both the drawing and the width-measuring go through the same new metrics (so every
computed box, button and centred label stays self-consistent).

## Method

- **`apps/_type.py`** — a Pillow text renderer, **pure numpy + Pillow (no cv2)**, so it is
  unit-testable on headless WSL/CI even though cv2/mediapipe stay host-only. It draws onto the BGR
  numpy canvas the console already composes. A font registry resolves, per `(family, weight)`:
  bundled static faces → the bundled IBM Plex Sans **variable** file (weight axis set at load) →
  a common **system** face (DejaVu / Segoe / Arial) → Pillow's built-in default, and `available()`
  reports whether real TTF is live.
- **Cached coverage masks.** The first draw of a string rasterises a grayscale alpha mask
  (`_mask`, `lru_cache`), keyed by `(string, family, weight, size)` — **independent of colour and
  position** — then every frame just alpha-composites that mask in the requested colour with numpy
  (`roi·(1−a) + colour·a`). The constant labels ("DIAGNOSTICS", "render load", FRONT/LEFT/…)
  rasterise once and are reused; only changing numbers re-raster. This is what keeps the per-frame
  cost down (see Result).
- **`apps/_console.py`** — `text()`/`text_w()` now map the legacy `(scale, thick, mono)` args to a
  TTF `(family, weight, size_px)` and delegate to `_type`, **falling back to cv2 Hershey** if
  Pillow/FreeType is absent (the console still runs on a stripped checkout). `scale→px` factors are
  tuned so a line keeps roughly its old footprint (fixed-width panels don't overflow) with one
  small bump answering "too small". The prominent chrome — buttons, the `DIAGNOSTICS` heading, the
  verdict pill, the radar cardinals — is routed to **Rajdhani** (`family="display"`) to match the
  mockup; readouts/HUD stay **IBM Plex Mono**; other labels **IBM Plex Sans**. No call-site in the
  two apps changed — the shared view carries it.
- **Bundled faces** under `apps/assets/fonts/` (IBM Plex Mono R/Medium/SemiBold static, IBM Plex
  Sans variable, Rajdhani SemiBold/Bold static) with each family's **SIL OFL-1.1** licence text.
- **Verification** reused the B6.3 recipe: a host script renders representative console frames
  (tracking + stats overlay, and clean) to PNG with a synthetic frame (no camera/audio — `compose`
  is pure drawing), run before (Hershey) and after (TTF), and inspected.
- **7 tests** (`tests/test_type.py`, skip if Pillow absent): faces resolve to the expected files,
  width is positive/monotonic, mono is fixed-advance (and sans is not), glyphs are drawn in the
  requested colour while the background margin is spared, empty/clipped/unknown-family inputs
  don't crash.

## Result

- **Qualitative:** the before/after PNGs are night-and-day — the stats panel, titlebar, radar
  labels, and buttons go from thin jagged strokes with uneven baselines to crisp, evenly-spaced
  IBM Plex / Rajdhani type. No panel overflow or clipping; the layout is unchanged.
- **Performance (host, `compose` with the stats panel ON, synthetic 960×720 frame):** Hershey
  **8.8 ms/frame** → naive TTF (PIL round-trip per string) **22 ms** → **cached-mask TTF 11.4 ms**,
  i.e. only **~2.6 ms** over Hershey. Comfortably inside the 33 ms budget at the C270's 30 fps, so
  the GUI loop (which shares the thread with pose detection) keeps its rate; the audio render
  worker is a separate thread and untouched.
- 351 tests pass (7 new), ruff clean. Pillow (HPND, permissive — donationware-safe) added to the
  `vision` extra **and** `dev` so the suite exercises `_type` on CI/WSL.

## Interpretation

The whole "unprofessional" impression was the rasteriser, not the design — swapping Hershey for
hinted TrueType of the mockup's own faces closed the gap with zero layout work, exactly because
`text()` and `text_w()` share the new metrics (self-consistent boxes). Caching the colour- and
position-independent **coverage mask** (not the composite, which varies with the live camera
background) is the key perf move: constant labels pay the FreeType cost once. Keeping it pure
numpy + Pillow (cv2 only in the Hershey fallback) preserves the project's rule that the host view
stays a thin layer and the logic stays testable headless.

## Learnings

- Hershey (cv2's only fonts) is the ceiling on how professional an OpenCV UI can look; real
  typography needs a TrueType rasteriser (Pillow) composited onto the canvas. Route *both* the
  draw and the width-measure through it so every computed box stays consistent and the layout
  survives the swap untouched.
- Cache the **coverage mask** keyed by `(text, family, weight, size)` — colour- and
  position-independent — and composite per frame with numpy; constant UI labels then rasterise
  once. The naive "convert ROI to PIL, draw, convert back" per string was 2.5× the Hershey cost;
  the cache brought it to +2.6 ms.
- A fidelity complaint ("looks low-res") on an already-approved design is a rasteriser/asset fix,
  not a redesign — no new mockup round; match the faces the mockup already specified.

## Decision / next

GUI type now matches the mockup. Recorded follow-ups (separate blocks, out of scope here):
**B6.10** route `live_tracker_audio.py`'s remaining raw `cv2.putText` overlays through the shared
`_console.text` for one consistent look; **B6.11** HiDPI / resizable window (`WINDOW_NORMAL` +
an integer canvas scale) so the console isn't small on high-DPI displays — distinct from this
glyph-sharpness fix.
