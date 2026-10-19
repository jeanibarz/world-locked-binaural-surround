---
name: host-gui-visual-check
description: >-
  Verify or debug anything VISUAL in the host GUI apps (Dear PyGui cockpit, Tk calibration GUI,
  live tracker window) — alignment, symmetry, centring, text size/width, clipping, DPI scaling,
  "it looks shifted/leans left/not mirrored" — by launching the app on the Windows host,
  screenshotting it DPI-aware, drawing the computed geometry as a debug overlay, and MEASURING
  pixels with Pillow instead of reasoning about the math or trusting an isolated probe. Use for
  any layout/label/radar/panel bug report, before and after a fix, and before claiming a render
  is "host-verified".
---

# Host GUI visual check

**Core principle (the visual twin of `audio-loopback-diagnostics`):** the math being symmetric
proves nothing about pixels. A label is placed by *three* things — the anchor point, the
measured box, and the rendered ink — and any of them can be wrong on its own. Overlay all three
in the running app, screenshot, and **measure each one** against a reference line; the defect
is wherever the numbers stop matching. Never diagnose by eye, by trigonometry, or by a probe that
does not share the app's exact setup. Never add a calibration constant to make a measurement
agree with another — two measurements of the same text that disagree are measuring *different
things* (B6.12: different fonts); find which.

## Recipe

1. **Launch on the host** (see CLAUDE.md "Running the host GUI apps"): `py.exe -3.12
   C:\Users\<user>\BinauralHeadtracker\launch_<app>.py` with `run_in_background`. The cockpit has
   `run_preview()` — synthetic, static speaker azimuths (±30/±110/±150), ideal for geometry.
   **Kill before every relaunch** (edits take effect on relaunch, windows otherwise pile up):
   `powershell.exe -NoProfile -Command "Get-Process | ? { \$_.MainWindowTitle -like '*cockpit*' } | Stop-Process -Force"`
2. **Screenshot DPI-aware, fixed size** — `spikes/host_window_shot.ps1` (raises + resizes the
   window so shots are comparable, declares the capturer per-monitor-DPI-aware so pixels match
   the app's):
   `powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(wslpath -w spikes/host_window_shot.ps1)" '*cockpit*' 'C:\Users\<user>\BinauralHeadtracker\shot.png' 2>&1 | grep -v UtilAcceptVsock`
   Then `Read` the PNG via `/mnt/c/...`.
3. **Draw a temporary debug overlay** in the draw code, in pure colours the palette never uses:
   red = the computed bounding box, green = the intended anchor/centre tick (outside the box, so
   it doesn't contaminate ink measurement), magenta = the reference axis (e.g. radar centre `cx`).
   Push elements clear of dots/rings if they overlap — occlusion corrupts the ink mask.
4. **Measure** with `spikes/host_shot_measure.py` (host `py.exe -3.12`, needs Pillow+numpy+scipy):
   `... SHOT.png zoom X0 Y0 X1 Y1 crop.png 3` to count pixels by eye;
   `... SHOT.png comps red --axis 874.3` / `comps white ...` to get every box / ink component's
   bbox and signed distance from the axis. Compare mirror pairs: anchor vs anchor, box vs box,
   ink-in-box vs ink-in-box. Quote the numbers (px) in the commit and the report.
5. Fix the root cause, re-shoot, re-measure, **remove the overlay**, `uv run ruff check`, commit.

## Rules that would have saved the B6.12 label loop

- **A probe is evidence only if it reproduces the app's setup**: same font *binding path*
  (not just the same TTF), same atlas size, same DPI awareness (a non-DPI-aware probe window is
  bitmap-scaled 1.5× by Windows on this box — sizes come out ×1.5 and blurry), same scale `k`.
  When a probe and the app disagree, the probe is wrong until proven otherwise.
- **Measure in the app first, probe second.** One overlay screenshot localises a defect in a
  single run; isolated probes only confirm hypotheses you already have.
- **A uniform offset does NOT preserve left/right symmetry.** "Every label leans 4 px left"
  moves the left label 4 px *further from* the axis and the right label 4 px *closer to* it — an
  8 px mirror error. Don't dismiss a constant offset as "affects both sides equally".
- **If ink is flush to one edge of its box, the box width is wrong, not the anchor.** Look at
  what fills the box before moving the anchor.

## Dear PyGui 2.x facts (verified on the host, dpg 2.3.1)

- `draw_text` has no `font=`; it renders with the font of the **enclosing container** (window /
  `child_window` / `group`), else the global `bind_font`. `bind_item_font(<drawlist>, …)` is
  **silently ignored** — bind to the container that holds the drawlist (the cockpit binds mono to
  its `canvas_host` child).
- `get_text_size(s, font=f)` returns the advance box at `f`'s atlas size; ImGui scales glyph
  advances linearly, so `m * size / atlas_px` **is** the rendered advance box to the pixel — when
  draw and measure use the same font. No calibration factor is ever needed; if one seems needed,
  the fonts differ.
- Text is anchored at its top-left; centre with `x = px - w/2`. IBM Plex Mono's ink is centred in
  its advance box to <0.5 px for these labels (`°`, `+`, `-` have symmetric side bearings).
- `get_text_size` only works after the first `render_dearpygui_frame()` built the atlas.
