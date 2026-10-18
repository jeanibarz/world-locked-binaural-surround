"""Anti-aliased TrueType text for the host console (B6.8).

OpenCV ships only Hershey single-stroke vector fonts — thin, jagged, undersized —
so the console type never matched the mockup (IBM Plex + Rajdhani); the owner saw it
as "low resolution, small, not professional". This module draws crisp, hinted
TrueType glyphs with Pillow straight onto the BGR numpy canvas the console already
composes, compositing each string against its **real local background** (an ROI
cut from the canvas) so anti-aliased edges blend correctly over panels, the camera
image, and the bars underneath.

Pure numpy + Pillow (no ``cv2``), so it is unit-testable on headless WSL/CI. The
fonts live under ``apps/assets/fonts/`` (SIL OFL-1.1; see that folder's ``OFL-*.txt``).
Resolution degrades gracefully: a bundled face → a common system face → Pillow's
built-in bitmap default, and :func:`available` reports whether real TTF rendering is
live so the console can fall back to Hershey if the whole stack is missing.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np

try:  # Pillow is a host/GUI-only dep (the `vision` extra); never required on CI's render path.
    from PIL import Image, ImageDraw, ImageFont, features

    _HAVE_PIL = bool(features.check("freetype2"))  # TTF needs FreeType; bitmap-only can't.
except Exception:  # noqa: BLE001 — any import failure just disables TTF and keeps Hershey.
    _HAVE_PIL = False

_FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"

# family -> weight-name -> (filename, variable-axis weight or None for a static file).
# mono = IBM Plex Mono (readouts/HUD), display = Rajdhani (title/buttons), sans = IBM Plex Sans
# (the variable file; the weight axis is set per load). The mockup's three faces exactly.
_STATIC = {
    ("mono", "regular"): "IBMPlexMono-Regular.ttf",
    ("mono", "medium"): "IBMPlexMono-Medium.ttf",
    ("mono", "semibold"): "IBMPlexMono-SemiBold.ttf",
    ("display", "semibold"): "Rajdhani-SemiBold.ttf",
    ("display", "bold"): "Rajdhani-Bold.ttf",
}
_SANS_VAR = "IBMPlexSans-var.ttf"
_WGHT = {"regular": 400, "medium": 500, "semibold": 600, "bold": 700}

# Common system fallbacks if the bundle is absent (Linux DejaVu, Windows). Keeps the console
# legible on a stripped checkout; the look is just not the brand face.
_SYS_FALLBACK = {
    "mono": ("DejaVuSansMono.ttf", "consola.ttf", "cour.ttf"),
    "sans": ("DejaVuSans.ttf", "segoeui.ttf", "arial.ttf"),
    "display": ("DejaVuSans-Bold.ttf", "segoeuib.ttf", "arialbd.ttf"),
}

available = (lambda: _HAVE_PIL)  # noqa: E731 — tiny predicate the console checks once.


@lru_cache(maxsize=256)
def _font(family: str, weight: str, size_px: int):
    """A cached PIL FreeType font for (family, weight, size), or ``None`` if unavailable."""
    if not _HAVE_PIL:
        return None
    size_px = max(8, int(size_px))
    # 1) bundled static face
    fname = _STATIC.get((family, weight))
    if fname and (_FONT_DIR / fname).is_file():
        try:
            return ImageFont.truetype(str(_FONT_DIR / fname), size=size_px)
        except Exception:  # noqa: BLE001
            pass
    # 2) bundled sans variable face (set the weight axis)
    if family == "sans" and (_FONT_DIR / _SANS_VAR).is_file():
        try:
            f = ImageFont.truetype(str(_FONT_DIR / _SANS_VAR), size=size_px)
            try:
                f.set_variation_by_axes([_WGHT.get(weight, 400)])
            except Exception:  # noqa: BLE001 — a non-variable build just stays Regular.
                pass
            return f
        except Exception:  # noqa: BLE001
            pass
    # 3) a common system face by bare name (Pillow searches the OS font dirs)
    for cand in _SYS_FALLBACK.get(family, _SYS_FALLBACK["sans"]):
        try:
            return ImageFont.truetype(cand, size=size_px)
        except Exception:  # noqa: BLE001
            continue
    # 4) Pillow's built-in bitmap default — ugly but never crashes
    try:
        return ImageFont.load_default()
    except Exception:  # noqa: BLE001
        return None


def text_width(s: str, size_px: int, *, family: str = "mono", weight: str = "regular") -> int:
    """Advance width of ``s`` in pixels (0 if text rendering is unavailable)."""
    f = _font(family, weight, size_px)
    if f is None or not s:
        return 0
    try:
        return int(round(f.getlength(s)))
    except Exception:  # noqa: BLE001
        return int(round(size_px * 0.6 * len(s)))


@lru_cache(maxsize=1024)
def _mask(s: str, family: str, weight: str, size_px: int):
    """A cached grayscale coverage mask for one string, independent of colour and position.

    Returns ``(alpha, ox, oy)``: ``alpha`` is an HxW float array in [0, 1]; ``(ox, oy)`` is the
    mask's top-left relative to the baseline-left draw point. Caching the FreeType raster here (not
    the composite, which varies with the live background and colour) is what keeps the per-frame
    cost low — the many constant console labels re-rasterise once, then only composite.
    """
    f = _font(family, weight, size_px)
    if f is None or not s:
        return None
    try:
        bl, t, r, b = f.getbbox(s, anchor="ls")
        anchor = "ls"
    except TypeError:  # very old Pillow without the anchor kwarg
        bl, t, r, b = f.getbbox(s)
        t -= size_px  # approximate a baseline box from the top-origin one
        b -= size_px
        anchor = None
    mw, mh = max(1, int(r - bl) + 2), max(1, int(b - t) + 2)
    im = Image.new("L", (mw, mh), 0)
    d = ImageDraw.Draw(im)
    ox, oy = int(bl) - 1, int(t) - 1  # mask (0,0) maps to image (x+ox, y+oy)
    if anchor:
        d.text((-ox, -oy), s, font=f, fill=255, anchor="ls")
    else:
        d.text((-ox, -oy - size_px), s, font=f, fill=255)
    return np.asarray(im, np.float32) / 255.0, ox, oy


def draw_text(img, s: str, x, y, color_bgr, size_px: int, *,
              family: str = "mono", weight: str = "regular") -> bool:
    """Draw ``s`` with its left edge at ``x`` and **baseline** at ``y`` (cv2.putText origin
    convention) onto the BGR ``img`` in place, anti-aliased against the local background.

    Returns ``True`` when drawn, ``False`` if TTF rendering is unavailable (so the caller can
    fall back to Hershey). Composites a cached coverage mask with numpy — only the glyph box is
    touched and the raster is reused frame to frame, so it is cheap per call.
    """
    if not _HAVE_PIL or not s:
        return False
    m = _mask(s, family, weight, int(size_px))
    if m is None:
        return False
    alpha, ox, oy = m
    x, y = int(round(x)), int(round(y))
    h, w = img.shape[:2]
    gx, gy = x + ox, y + oy  # mask top-left in image coords
    mh, mw = alpha.shape
    sx, sy = max(0, -gx), max(0, -gy)              # crop into the mask when off the top/left
    dx0, dy0 = max(0, gx), max(0, gy)
    dx1, dy1 = min(w, gx + mw), min(h, gy + mh)
    if dx1 <= dx0 or dy1 <= dy0:
        return True  # fully clipped — handled, nothing to paint
    a = alpha[sy:sy + (dy1 - dy0), sx:sx + (dx1 - dx0), None]
    roi = img[dy0:dy1, dx0:dx1].astype(np.float32)
    col = np.array((float(color_bgr[0]), float(color_bgr[1]), float(color_bgr[2])), np.float32)
    img[dy0:dy1, dx0:dx1] = (roi * (1.0 - a) + col * a + 0.5).astype(np.uint8)
    return True
