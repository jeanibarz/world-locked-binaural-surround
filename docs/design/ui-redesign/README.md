# Cockpit UI redesign — design reference

Owner-provided redesign mockups for the cockpit (`apps/cockpit_view.py` /
`binaural_headtracker/cockpit.py`), exported from a visual design tool as
standalone React/HTML pages. **Reference mockups, not production code** — the
exact values (colors, fonts, spacing, radii, layout) live in the inline
`style="…"` attributes of the `.dc.html` files.

## Screens
- `main/Main.dc.html` — the main window (1280×820): title bar; row 1 =
  camera + SYSTEM | world-locked ring (radar) | latency (4-segment bar +
  distribution); row 3 = experiments drawer (Blind A/B · Localization · Test
  sources).
- `settings/D-EngineSettings.dc.html` — the Settings dialog (560×1000),
  opened from the main window: Camera, Engine, Audio output, Spatial filter
  (HRIR), Headphone compensation, with Reset/Cancel/Apply.

## Viewing
Serve the folder and open the `.dc.html` (file:// blocks the scripts):
`python3 -m http.server` in `main/` or `settings/`.

## Palette (extracted)
bg `#0d1117` · panel `#151c28` · border `#243044`/`#2a3547` · text `#e6edf6` ·
muted `#b3bfd2`/`#5b6678` · cyan `#35d0d0` · amber `#ffb340` · green `#4cd98a` ·
red `#ff6b6b` · titlebar `#f1f3f6`. Fonts: Inter (UI), JetBrains Mono (numbers).
