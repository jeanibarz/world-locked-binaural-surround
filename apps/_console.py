"""Shared OpenCV renderer for the VR-style console (B6.3) — host-side drawing only.

One place for the look both host GUIs wear: the calibration console and the world-lock
passthrough. It draws, on a BGR canvas, the mockup's components — a titlebar with a live
status pill, the camera panel (subtle scanlines, face box + lock tag, a yaw/pitch/roll HUD),
the top-down azimuth radar (rings, cardinal labels, forward-FOV cone, the nearest speaker
highlighted with a halo and a dashed pointer, a listener arrow, a legend), a bottom controls
bar (readout chips + key-hint buttons), and a toggleable **diagnostics overlay** fed by
:mod:`binaural_headtracker.diagnostics`.

Pure drawing on numpy arrays with `cv2`; no camera/audio/state here, so it stays a thin,
reusable view. The palette is the mockup's, in OpenCV BGR. cv2 only has Hershey fonts, so the
type differs from the HTML mock, but the layout, palette and components match.
"""

from __future__ import annotations

import math

import _type  # TTF text rendering (B6.8); falls back to Hershey if Pillow/fonts are absent
import numpy as np

from binaural_headtracker.diagnostics import Diagnosis, Health
from binaural_headtracker.radar import azimuth_to_radar_xy

# Mockup palette, OpenCV BGR.
BG = (23, 16, 13)        # #0d1017
PANEL = (36, 26, 21)     # #151a24
PANEL2 = (48, 34, 27)    # #1b2230
LINE = (68, 51, 42)      # #2a3344
FG = (245, 237, 232)     # #e8edf5
MUTED = (171, 151, 139)  # #8b97ab
FAINT = (138, 112, 100)  # #64708a
ACCENT = (214, 214, 56)  # #38d6d6 tracking cyan
ACTIVE = (75, 178, 246)  # #f6b24b active/recenter amber
GOOD = (140, 217, 84)    # #54d98c
WARN = (75, 178, 246)    # amber, reuse ACTIVE for "tight"
RISK = (60, 140, 246)    # orange
RED = (91, 84, 242)      # #f2545b failing

_F = None  # cv2 font, set lazily


def _cv2():
    import cv2
    return cv2


def _font():
    return _cv2().FONT_HERSHEY_SIMPLEX


def health_color(h: Health):
    return {Health.OK: GOOD, Health.TIGHT: ACTIVE, Health.RISK: RISK, Health.FAILING: RED}[h]


def apply_camera_settings(cap, *, width=0, height=0, fps=0, mjpg=False):
    """Apply optional camera settings (0 = leave the driver default) and return the actual
    (width, height, fps) the driver negotiated. Harmless if a request isn't honoured."""
    cv2 = _cv2()
    try:
        if mjpg:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        if width:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(width))
        if height:
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(height))
        if fps:
            cap.set(cv2.CAP_PROP_FPS, int(fps))
    except Exception:  # noqa: BLE001 — a camera that rejects a set() should not crash startup
        pass
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    f = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    return w, h, f


# The console speaks in cv2 `scale`; TTF speaks in pixels. These factors convert one to the
# other, chosen so a line keeps roughly its old footprint (fixed-width panels don't overflow)
# while the glyphs become crisp, hinted TrueType — mono a touch tighter than sans, matching the
# two faces. One small global bump answers the owner's "too small" without breaking the layout.
_PX_MONO = 32.0
_PX_SANS = 36.0


def _ttf_spec(scale, thick, mono, family):
    """Map the legacy (scale, thick, mono) args to a TTF (family, weight, size_px)."""
    if family is None:
        family = "mono" if mono else "sans"
    px = scale * (_PX_MONO if family == "mono" else _PX_SANS)
    weight = "semibold" if thick >= 2 else "regular"
    return family, weight, int(round(px))


def text(img, s, x, y, color, scale=0.42, thick=1, *, mono=False, family=None):
    """Draw text at baseline-left (x, y). Uses bundled TrueType faces (IBM Plex / Rajdhani);
    falls back to cv2 Hershey only if TTF rendering is unavailable. ``family`` overrides the
    mono/sans choice (e.g. ``"display"`` for Rajdhani headings/buttons)."""
    fam, weight, px = _ttf_spec(scale, thick, mono, family)
    if _type.draw_text(img, s, x, y, color, px, family=fam, weight=weight):
        return
    cv2 = _cv2()
    font = cv2.FONT_HERSHEY_PLAIN if mono else cv2.FONT_HERSHEY_SIMPLEX
    sc = scale * 1.25 if mono else scale
    cv2.putText(img, s, (int(x), int(y)), font, sc, color, thick, cv2.LINE_AA)


def text_w(s, scale=0.42, thick=1, *, mono=False, family=None):
    """Advance width in px of the string as :func:`text` would draw it (TTF metrics; Hershey
    fallback matches the fallback draw)."""
    fam, weight, px = _ttf_spec(scale, thick, mono, family)
    if _type.available():
        return _type.text_width(s, px, family=fam, weight=weight)
    cv2 = _cv2()
    font = cv2.FONT_HERSHEY_PLAIN if mono else cv2.FONT_HERSHEY_SIMPLEX
    sc = scale * 1.25 if mono else scale
    (w, _), _ = cv2.getTextSize(s, font, sc, thick)
    return w


def rrect(img, x, y, w, h, color, *, fill=True, r=8, thick=1):
    """Rounded rectangle (filled or stroked)."""
    cv2 = _cv2()
    x, y, w, h, r = int(x), int(y), int(w), int(h), int(r)
    r = max(0, min(r, w // 2, h // 2))
    if fill:
        cv2.rectangle(img, (x + r, y), (x + w - r, y + h), color, -1)
        cv2.rectangle(img, (x, y + r), (x + w, y + h - r), color, -1)
        corners = ((x + r, y + r), (x + w - r, y + r),
                   (x + r, y + h - r), (x + w - r, y + h - r))
        for cx, cy in corners:
            cv2.circle(img, (cx, cy), r, color, -1, cv2.LINE_AA)
    else:
        cv2.line(img, (x + r, y), (x + w - r, y), color, thick, cv2.LINE_AA)
        cv2.line(img, (x + r, y + h), (x + w - r, y + h), color, thick, cv2.LINE_AA)
        cv2.line(img, (x, y + r), (x, y + h - r), color, thick, cv2.LINE_AA)
        cv2.line(img, (x + w, y + r), (x + w, y + h - r), color, thick, cv2.LINE_AA)
        for cx, cy, a0 in ((x + r, y + r, 180), (x + w - r, y + r, 270),
                           (x + w - r, y + h - r, 0), (x + r, y + h - r, 90)):
            cv2.ellipse(img, (cx, cy), (r, r), a0, 0, 90, color, thick, cv2.LINE_AA)


def _blend(img, overlay, alpha):
    _cv2().addWeighted(overlay, alpha, img, 1 - alpha, 0, img)


# ---------------------------------------------------------------- titlebar

def titlebar(canvas, x, y, w, h, title, live_text, live_color, *, phase=0.0, method=None):
    cv2 = _cv2()
    cv2.rectangle(canvas, (x, y), (x + w, y + h), PANEL2, -1)
    cv2.line(canvas, (x, y + h), (x + w, y + h), LINE, 1, cv2.LINE_AA)
    cy = y + h // 2
    for i in range(3):
        cv2.circle(canvas, (x + 14 + i * 16, cy), 5, LINE, -1, cv2.LINE_AA)
    text(canvas, title, x + 60, cy + 4, FG, 0.44, mono=True)
    if method:
        # persistent amber chip — the SHARED interpolation method, shown whether or not a test runs
        chip = f"method: {method}"
        cw = text_w(chip, 0.4, mono=True)
        chx = x + 60 + text_w(title, 0.44, mono=True) + 18
        rrect(canvas, chx, cy - 10, cw + 14, 20, PANEL, r=5)
        rrect(canvas, chx, cy - 10, cw + 14, 20, ACTIVE, fill=False, r=5, thick=1)
        text(canvas, chip, chx + 7, cy + 4, ACTIVE, 0.4, mono=True)
    if live_text:
        pill = f"{live_text}"
        tw = text_w(pill, 0.42, mono=True)
        px = x + w - tw - 26
        # pulsing dot (phase 0..1)
        rad = 4 + int(2 * (0.5 + 0.5 * np.sin(phase * 2 * np.pi)))
        cv2.circle(canvas, (px - 10, cy), rad, live_color, -1, cv2.LINE_AA)
        cv2.circle(canvas, (px - 10, cy), 7, live_color, 1, cv2.LINE_AA)
        text(canvas, pill, px, cy + 4, live_color, 0.42, mono=True)


# ---------------------------------------------------------------- camera panel

def _hud(frame, yaw, pitch, roll):
    cv2 = _cv2()
    h = frame.shape[0]
    for i, (lbl, val) in enumerate([("yaw", yaw), ("pitch", pitch), ("roll", roll)]):
        y = h - 60 + i * 20
        text(frame, lbl, 14, y, MUTED, 0.5, mono=True)
        x0 = 66
        rrect(frame, x0, y - 9, 100, 7, (40, 40, 40), r=3)
        cv2.line(frame, (x0 + 50, y - 11), (x0 + 50, y - 1), (150, 150, 150), 1, cv2.LINE_AA)
        px = int(x0 + 50 + np.clip(val, -45, 45) / 45 * 48)
        cv2.line(frame, (px, y - 12), (px, y), ACTIVE, 2, cv2.LINE_AA)
        text(frame, f"{val:+5.1f}", x0 + 112, y, ACCENT, 0.5, mono=True)


def camera_panel(frame, *, pose=None, landmarks=None, lock=None, mirror=True, scanlines=True):
    """Annotate a (already display-oriented) camera frame in place: scanlines, face box +
    lock tag, mesh dots, and the yaw/pitch/roll HUD. ``landmarks`` are MediaPipe normalized
    landmarks from the *un-mirrored* frame (x flipped here when ``mirror``)."""
    cv2 = _cv2()
    h, w = frame.shape[:2]
    if scanlines:
        ov = frame.copy()
        for yy in range(0, h, 4):
            cv2.line(ov, (0, yy), (w, yy), (255, 255, 255), 1)
        _blend(frame, ov, 0.03)
    xs = ys = None
    if landmarks is not None:
        pts = [((1.0 - lm.x) if mirror else lm.x, lm.y) for lm in landmarks]
        for nx, ny in pts:
            cv2.circle(frame, (int(nx * w), int(ny * h)), 1, ACCENT, -1, cv2.LINE_AA)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
    if xs:
        x0, x1 = int(min(xs) * w) - 10, int(max(xs) * w) + 10
        y0, y1 = int(min(ys) * h) - 14, int(max(ys) * h) + 10
        rrect(frame, x0, y0, x1 - x0, y1 - y0, ACCENT, fill=False, r=6, thick=1)
        tag = f"face - lock {lock:.2f}" if lock is not None else "face"
        tw = text_w(tag, 0.38, mono=True)
        rrect(frame, x0, y0 - 16, tw + 10, 15, (0, 0, 0), r=3)
        text(frame, tag, x0 + 5, y0 - 4, ACCENT, 0.38, mono=True)
    if pose is not None:
        _hud(frame, pose.yaw, pose.pitch, pose.roll)


# ---------------------------------------------------------------- radar

def _rot(px, py, deg):
    """Rotate a point about the origin by ``deg`` (screen convention, y-down)."""
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return px * c - py * s, px * s + py * c


def radar(canvas, cx, cy, R, world_az, channels, facing_i, *, head_deg=0.0,
          source_az=None, legend=True):
    """World-frame radar: the speakers sit **fixed** at their world azimuths and the **head**
    (centre arrow + gaze cone) rotates, so the world really looks world-locked. ``world_az`` is
    the fixed per-channel world azimuth; ``head_deg`` is where the head faces in the world;
    ``facing_i`` is the speaker nearest the gaze; ``source_az`` (optional) marks a test source."""
    cv2 = _cv2()
    for f in (1.0, 0.66, 0.33):
        cv2.circle(canvas, (cx, cy), int(R * f), LINE, 1, cv2.LINE_AA)
    # gaze field-of-view cone, centred on where the head faces (±28°)
    ov = canvas.copy()
    poly = [(cx, cy)]
    for d in range(-28, 29, 4):
        x, y = azimuth_to_radar_xy(head_deg + d, R, cx, cy)
        poly.append((int(x), int(y)))
    cv2.fillPoly(ov, [np.array(poly, np.int32)], ACCENT)
    _blend(canvas, ov, 0.10)
    # cardinal labels are WORLD directions (fixed)
    for lbl, az in (("FRONT", 0), ("LEFT", 90), ("BACK", 180), ("RIGHT", -90)):
        x, y = azimuth_to_radar_xy(float(az), R + 18, cx, cy)
        text(canvas, lbl, x - text_w(lbl, 0.4, thick=2, family="display") / 2, y + 4,
             MUTED, 0.4, thick=2, family="display")
    # fixed world speakers
    for i, ch in enumerate(channels):
        x, y = azimuth_to_radar_xy(float(world_az[i]), R, cx, cy)
        on = i == facing_i
        col = ACTIVE if on else ACCENT
        if on:
            cv2.circle(canvas, (int(x), int(y)), 14, ACTIVE, 1, cv2.LINE_AA)
        cv2.circle(canvas, (int(x), int(y)), 8 if on else 5, col, -1, cv2.LINE_AA)
        cv2.circle(canvas, (int(x), int(y)), 8 if on else 5, PANEL, 1, cv2.LINE_AA)
        tx, ty = azimuth_to_radar_xy(float(world_az[i]), R - 20, cx, cy)
        text(canvas, ch, tx - text_w(ch, 0.34, mono=True) / 2, ty + 4,
             ACTIVE if on else MUTED, 0.34, mono=True)
    # test source marker (a world-fixed sound you place for A/B)
    if source_az is not None:
        sx, sy = azimuth_to_radar_xy(float(source_az), R, cx, cy)
        _dashed(canvas, (cx, cy), (int(sx), int(sy)), GOOD)
        cv2.circle(canvas, (int(sx), int(sy)), 9, GOOD, -1, cv2.LINE_AA)
        cv2.circle(canvas, (int(sx), int(sy)), 13, GOOD, 1, cv2.LINE_AA)
        text(canvas, "src", int(sx) - text_w("src", 0.3, mono=True) / 2, int(sy) + 3,
             BG, 0.3, mono=True)
    # head: centre arrow rotated to the gaze direction (φ = −head_deg maps "up" → head_deg)
    base = [(0, -13), (8, 8), (0, 3), (-8, 8)]
    arrow = np.array([[int(cx + a), int(cy + b)]
                      for a, b in (_rot(px, py, -head_deg) for px, py in base)])
    cv2.fillPoly(canvas, [arrow], FG)
    if legend:
        ly = cy + R + 34
        cv2.circle(canvas, (cx - 120, ly - 4), 5, ACTIVE, -1, cv2.LINE_AA)
        text(canvas, "facing", cx - 108, ly, MUTED, 0.34)
        arr = np.array([[cx - 20, ly - 9], [cx - 14, ly], [cx - 26, ly]])
        cv2.fillPoly(canvas, [arr], FG)
        text(canvas, "head", cx - 8, ly, MUTED, 0.34)
        if source_az is not None:
            cv2.circle(canvas, (cx + 58, ly - 4), 5, GOOD, -1, cv2.LINE_AA)
            text(canvas, "test", cx + 68, ly, MUTED, 0.34)


def _dashed(canvas, p0, p1, color, dash=6):
    cv2 = _cv2()
    p0 = np.array(p0, float)
    p1 = np.array(p1, float)
    d = p1 - p0
    n = int(np.hypot(*d) / dash)
    if n < 1:
        return
    step = d / n
    for i in range(0, n, 2):
        a = p0 + step * i
        b = p0 + step * min(i + 1, n)
        cv2.line(canvas, (int(a[0]), int(a[1])), (int(b[0]), int(b[1])), color, 1, cv2.LINE_AA)


# ---------------------------------------------------------------- controls bar

def controls_bar(canvas, x, y, w, h, readout, buttons):
    """`readout` = list of (label, value, value_color). `buttons` = list of (text, key, accent)."""
    cv2 = _cv2()
    cv2.rectangle(canvas, (x, y), (x + w, y + h), PANEL2, -1)
    cv2.line(canvas, (x, y), (x + w, y), LINE, 1, cv2.LINE_AA)
    cyb = y + h // 2
    tx = x + 16
    for lbl, val, vcol in readout:
        text(canvas, lbl, tx, cyb + 4, MUTED, 0.4, mono=True)
        tx += text_w(lbl, 0.4, mono=True) + 6
        text(canvas, val, tx, cyb + 4, vcol, 0.42, mono=True)
        tx += text_w(val, 0.42, mono=True) + 20
    # buttons right-aligned
    bx = x + w - 14
    for btxt, key, accent in reversed(buttons):
        label = f"{btxt} [{key}]"
        bw = text_w(label, 0.46, thick=2, family="display") + 24
        col = ACTIVE if accent else FG
        bgc = (int(ACTIVE[0] * 0.18 + PANEL[0]), int(ACTIVE[1] * 0.18 + PANEL[1]),
               int(ACTIVE[2] * 0.18 + PANEL[2])) if accent else PANEL
        rrect(canvas, bx - bw, cyb - 15, bw, 30, bgc, r=7)
        rrect(canvas, bx - bw, cyb - 15, bw, 30, ACTIVE if accent else LINE, fill=False, r=7)
        text(canvas, label, bx - bw + 12, cyb + 5, col, 0.46, thick=2, family="display")
        bx -= bw + 10


# ---------------------------------------------------------------- diagnostics overlay

def _bar(canvas, x, y, w, h, frac, color, *, peak=None):
    cv2 = _cv2()
    rrect(canvas, x, y, w, h, (40, 40, 40), r=h // 2)
    fw = int(np.clip(frac, 0, 1) * w)
    if fw > 0:
        rrect(canvas, x, y, max(fw, h), h, color, r=h // 2)
    if peak is not None:
        pxp = x + int(np.clip(peak, 0, 1) * w)
        cv2.line(canvas, (pxp, y - 2), (pxp, y + h + 2), FG, 1, cv2.LINE_AA)


def stats_panel(canvas, x, y, w, h, d: Diagnosis, *, audio=True, extra_rate_hz=0.0):
    """Draw the toggleable diagnostics overlay at (x,y,w,h) over the console."""
    cv2 = _cv2()
    ov = canvas.copy()
    rrect(ov, x, y, w, h, PANEL, r=12)
    _blend(canvas, ov, 0.92)
    rrect(canvas, x, y, w, h, LINE, fill=False, r=12)
    pad = 16
    cxl = x + pad
    yy = y + 30
    text(canvas, "DIAGNOSTICS", cxl, yy, ACCENT, 0.52, thick=2, family="display")
    # verdict pill
    hc = health_color(d.health)
    label = d.health.value.upper()
    pw = text_w(label, 0.52, thick=2, family="display") + 22
    rrect(canvas, x + w - pad - pw, yy - 16, pw, 22, hc, r=11)
    text(canvas, label, x + w - pad - pw + 11, yy, BG, 0.52, thick=2, family="display")
    yy += 16
    cv2.line(canvas, (cxl, yy), (x + w - pad, yy), LINE, 1, cv2.LINE_AA)
    yy += 20
    # Render config — what you are actually running (engine · method · grid · test state),
    # and the block size in samples + ms, so it is never a guess.
    if getattr(d, "render_label", ""):
        for line in _wrap(d.render_label, int((w - 2 * pad) / 7)):
            text(canvas, line, cxl, yy, FG, 0.4, mono=True)
            yy += 16
    if getattr(d, "block_size", 0):
        khz = d.samplerate / 1000.0 if d.samplerate else 0.0
        text(canvas, f"block {d.block_size} smp  ({d.block_ms:.1f} ms)  @ {khz:.1f} kHz",
             cxl, yy, MUTED, 0.38, mono=True)
        yy += 18
    yy += 6

    if audio:
        # Head -> ear (the headline number), big. Measured from the live pose-age probe (B8.5)
        # when samples exist, else the stage-sum estimate.
        measured = getattr(d, "end_to_end_measured", False)
        text(canvas, "head-move -> ear", cxl, yy, MUTED, 0.42, mono=True)
        text(canvas, f"~{d.end_to_end_ms:5.0f} ms", cxl + 150, yy + 2, FG, 0.62)
        text(canvas, "(measured p50)" if measured else "(estimate)",
             cxl + 270, yy, GOOD if measured else FAINT, 0.36, mono=True)
        yy += 22
        if measured and getattr(d, "pose_age_n", 0):
            # measured split — which stage owns the latency: stale (camera) / queue (FIFO) / device
            text(canvas, f"  stale {d.pose_age_stale_ms:.0f} + queue {d.pose_age_queue_ms:.0f} + "
                 f"dev {d.pose_age_device_ms:.0f}   p95 {d.pose_age_p95_ms:.0f} p99 "
                 f"{d.pose_age_p99_ms:.0f} ms", cxl, yy, FAINT, 0.36, mono=True)
        else:
            text(canvas, f"  tracking ~{d.tracking_ms:.0f} - render+buffer ~"
                 f"{d.audio_out_latency_ms + d.render_ms:.0f} ms", cxl, yy, FAINT, 0.36, mono=True)
        yy += 26
        # render load bar
        text(canvas, "render load", cxl, yy, MUTED, 0.42, mono=True)
        _bar(canvas, cxl + 120, yy - 9, 180, 9, d.render_load, health_color(d.health),
             peak=d.render_load_peak)
        text(canvas, f"{d.render_load * 100:.0f}% (pk {d.render_load_peak * 100:.0f}%)",
             cxl + 312, yy, FG, 0.38, mono=True)
        yy += 22
        # Split 7.1 vs test source when the test is adding load, so "test tips it over" is visible.
        load_test = getattr(d, "render_load_test", 0.0)
        if load_test > 0.005:
            load_main = max(0.0, d.render_load - load_test)
            text(canvas, f"  7.1 {load_main * 100:.0f}%  +  test {load_test * 100:.0f}%"
                 f"   budget {d.block_ms:.1f} ms/block", cxl, yy, FAINT, 0.36, mono=True)
        else:
            text(canvas, f"  budget {d.block_ms:.1f} ms/block", cxl, yy, FAINT, 0.36, mono=True)
        yy += 24
        # buffer fill bar
        frac = d.fifo_fill_blocks / (2 * d.cushion_blocks) if d.cushion_blocks else 0.0
        text(canvas, "out buffer", cxl, yy, MUTED, 0.42, mono=True)
        _bar(canvas, cxl + 120, yy - 9, 180, 9, frac, GOOD if d.fifo_fill_blocks >= 1 else RED)
        text(canvas, f"{d.fifo_fill_blocks:.0f}/{d.cushion_blocks} blk", cxl + 312, yy,
             FG, 0.38, mono=True)
        yy += 22
        over = getattr(d, "recent_overflows", 0)
        dc = RED if (d.recent_underruns or over) else GOOD
        text(canvas, "dropouts", cxl, yy, MUTED, 0.42, mono=True)
        txt = f"{d.recent_underruns} recent"
        if over:
            txt += f"  +{over} overflow"  # clock-drift drops (e.g. WASAPI exclusive)
        text(canvas, txt, cxl + 120, yy, dc, 0.42, mono=True)
        yy += 26
    else:
        text(canvas, "audio not running (calibration only)", cxl, yy, FAINT, 0.4, mono=True)
        yy += 24
    # Tracker / camera pipeline (shown in both audio and calibration modes).
    rate = 1000.0 / d.loop_ms if d.loop_ms > 0 else extra_rate_hz
    text(canvas, "tracker", cxl, yy, MUTED, 0.42, mono=True)
    loop_txt = f"{rate:.0f} Hz" + (f"  ({d.loop_ms:.0f} ms/frame)" if d.loop_ms > 0 else "")
    text(canvas, loop_txt, cxl + 120, yy, ACCENT, 0.42, mono=True)
    yy += 20
    if d.detect_ms > 0:
        text(canvas, "detect", cxl, yy, MUTED, 0.42, mono=True)
        text(canvas, f"{d.detect_ms:.1f} ms/frame", cxl + 120, yy, FG, 0.4, mono=True)
        yy += 20
    if d.cam_w and d.cam_h:
        text(canvas, "camera", cxl, yy, MUTED, 0.42, mono=True)
        cam_txt = f"{d.cam_w}x{d.cam_h}" + (f" @ {d.camera_fps:.0f} fps" if d.camera_fps else "")
        text(canvas, cam_txt, cxl + 120, yy, FG, 0.4, mono=True)
        yy += 20
    if d.tracking_note:
        for line in _wrap(d.tracking_note, int((w - 2 * pad) / 7)):
            text(canvas, line, cxl, yy, FAINT, 0.36, mono=True)
            yy += 15
    yy += 6
    cv2.line(canvas, (cxl, yy - 6), (x + w - pad, yy - 6), LINE, 1, cv2.LINE_AA)
    yy += 6
    for r in d.reasons[:2]:
        for line in _wrap(r, int((w - 2 * pad) / 7)):
            text(canvas, line, cxl, yy, MUTED, 0.36, mono=True)
            yy += 15


def _wrap(s, width):
    words = s.split()
    out, cur = [], ""
    for wd in words:
        if len(cur) + len(wd) + 1 > width:
            out.append(cur)
            cur = wd
        else:
            cur = f"{cur} {wd}".strip()
    if cur:
        out.append(cur)
    return out or [s]


# ---------------------------------------------------------------- layout A compose

TITLE_H = 34
CTRL_H = 48
CAM_W, CAM_H = 600, 460
RAD_W = 380


RADAR_R = 140


def radar_center():
    return (CAM_W + RAD_W // 2, TITLE_H + CAM_H // 2 - 10)


def radar_az_at(mx, my, *, allow_outside=False):
    """World azimuth (deg) for a canvas point on the radar, else ``None``.

    Inverse of ``azimuth_to_radar_xy`` (``x = cx − R·sinβ``, ``y = cy − R·cosβ``), for
    click-to-place / drag in the world-frame radar. A click must land on the ring
    (``allow_outside=False``); a **drag** passes ``allow_outside=True`` so staying a little
    past the ring still tracks the cursor's angle (only the dead-centre is ignored).
    """
    cx, cy = radar_center()
    r = math.hypot(mx - cx, my - cy)
    if r < 6 or (not allow_outside and r > RADAR_R * 1.3):
        return None
    return math.degrees(math.atan2(cx - mx, cy - my))


def compose(frame, *, title, live, live_color=GOOD, pose=None, landmarks=None, lock=None,
            world_az=None, channels=None, facing_i=0, head_deg=0.0, source_az=None,
            readout=(), buttons=(), diagnosis=None, method=None,
            show_stats=False, audio_stats=True, rate_hz=0.0, phase=0.0):
    """Assemble the full Layout-A console canvas (titlebar · camera | radar · controls).

    ``frame`` is the display-oriented camera image (annotated in place with the HUD/face box).
    Returns a new BGR canvas sized ``(TITLE_H+CAM_H+CTRL_H, CAM_W+RAD_W, 3)``.
    """
    cv2 = _cv2()
    W, H = CAM_W + RAD_W, TITLE_H + CAM_H + CTRL_H
    canvas = np.full((H, W, 3), BG, np.uint8)
    titlebar(canvas, 0, 0, W, TITLE_H, title, live, live_color, phase=phase, method=method)
    # camera (left)
    camera_panel(frame, pose=pose, landmarks=landmarks, lock=lock)
    canvas[TITLE_H:TITLE_H + CAM_H, 0:CAM_W] = cv2.resize(frame, (CAM_W, CAM_H))
    # radar panel (right)
    cv2.rectangle(canvas, (CAM_W, TITLE_H), (W, TITLE_H + CAM_H), PANEL, -1)
    cv2.line(canvas, (CAM_W, TITLE_H), (CAM_W, TITLE_H + CAM_H), LINE, 1, cv2.LINE_AA)
    if world_az is not None and channels is not None:
        rcx, rcy = radar_center()
        radar(canvas, rcx, rcy, RADAR_R, world_az, channels, facing_i,
              head_deg=head_deg, source_az=source_az)
    controls_bar(canvas, 0, TITLE_H + CAM_H, W, CTRL_H, list(readout), list(buttons))
    if show_stats and diagnosis is not None:
        # Overlay the diagnostics on the camera (left) so the radar stays visible while diagnosing.
        sw, sh = 420, 356  # taller: render-config + block line + 7.1/test load split (B6.7)
        stats_panel(canvas, 14, TITLE_H + 14, sw, sh, diagnosis,
                    audio=audio_stats, extra_rate_hz=rate_hz)
    return canvas
