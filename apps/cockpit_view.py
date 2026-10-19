"""Dear PyGui **cockpit** — the native host view for the B6.12 overhaul (slice 1).

A second host front-end (the OpenCV :mod:`apps._console` stays the default and untouched). It is
the drawing shell for :mod:`binaural_headtracker.cockpit`: given a :class:`CockpitView` (pure data
from ``build_view``) and an optional camera RGB frame, it paints the cockpit — a prominent camera
feed, a world-locked azimuth radar, the **head-move-to-ear latency** headline with its live
distribution and stale/queue/device waterfall, status bars, and *realtime engine controls*
(interpolation method + CPU/Rust/GPU backend, output cushion, predict). Dear PyGui is MIT-licensed,
so it fits the donationware goal with no strings (unlike Qt's LGPL); see journal 48.

Dear PyGui is imported lazily, exactly as :mod:`apps._console` imports ``cv2`` — the module stays
importable (and the package's tests stay green) on headless WSL/CI where neither Dear PyGui nor a
display exists. The cockpit is drawn as **one full-redraw drawlist** (the whole canvas is cleared
and repainted each frame from the :class:`CockpitView`), the same discipline as the cv2 console's
``compose``: no per-widget state to keep in sync, so the view is a faithful function of the model.
Interactive controls sit beneath the canvas as real Dear PyGui widgets whose callbacks the live app
wires up (``on_method``/``on_backend``/``on_cushion``/``on_predict``/``on_recenter``/``on_quit``);
slice 1 ships the window + a synthetic **preview** so the look can be judged on the host before the
live camera/audio engine is wired in (slice 2).

Run the preview on the Windows host (see AGENTS.md "Running the host GUI apps")::

    py -3.12 -c "import cockpit_view; cockpit_view.run_preview()"
    # or, once installed:  py -3.12 apps/cockpit_view.py
"""

from __future__ import annotations

import math
from pathlib import Path

from binaural_headtracker import cockpit as M
from binaural_headtracker.radar import azimuth_to_radar_xy

_FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"

# Canvas geometry (logical px; the viewport is sized to fit it + the control strip).
CANVAS_W, CANVAS_H = 1012, 508
CAM_X, CAM_Y, CAM_W, CAM_H = 12, 12, 470, 300   # a touch narrower → more room for the panels
RAD_X, RAD_Y, RAD_W, RAD_H = 496, 12, 300, 300
LAG_X, LAG_Y, LAG_W, LAG_H = 810, 12, 190, 300  # wider STATUS box so its readouts fit on one line
PANEL_Y = 324  # the wide latency panel under the camera/radar
CONTROLS_H = 74  # height reserved below the scaled canvas for the two native controls rows
MARGIN = 10      # window padding kept around the scaled canvas


def available() -> bool:
    """True if Dear PyGui can be imported (host with the ``gui`` extra); False on CI/WSL."""
    try:
        import dearpygui.dearpygui  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _c(rgba: M.RGBA) -> list[int]:
    return [int(v) for v in rgba]


def _dpg_font_path(path: Path) -> str:
    """Return a path Dear PyGui's font loader can actually open.

    On the Windows host the app imports from the WSL checkout over a ``\\\\wsl.localhost\\…`` UNC
    path, and Dear PyGui's TTF loader (like MediaPipe's model loader; see AGENTS.md) fails on UNC.
    Python itself reads UNC fine, so copy the face to a local temp dir once and hand DPG that. A
    normal local path (WSL/CI, where fonts live under the repo) is returned unchanged.
    """
    s = str(path)
    if not s.startswith("\\\\"):  # not a UNC path → load in place
        return s
    import shutil
    import tempfile
    cache = Path(tempfile.gettempdir()) / "bht_fonts"
    cache.mkdir(parents=True, exist_ok=True)
    local = cache / path.name
    if not local.exists() or local.stat().st_size != path.stat().st_size:
        shutil.copyfile(path, local)  # Python reads the UNC source fine
    return str(local)


def _frame_to_texture(rgb, tw: int, th: int):
    """A HxWx3 uint8 RGB frame → a flat RGBA float list (0..1) at ``tw×th`` for a dynamic texture.

    Nearest-neighbour resize in numpy so the view needs no ``cv2`` (the cockpit may run in the
    calibration-only mode where cv2 is present anyway, but keeping this cv2-free costs nothing)."""
    import numpy as np

    h, w = rgb.shape[:2]
    ys = (np.arange(th) * h // th).clip(0, h - 1)
    xs = (np.arange(tw) * w // tw).clip(0, w - 1)
    small = rgb[ys][:, xs].astype(np.float32) / 255.0
    rgba = np.empty((th, tw, 4), np.float32)
    rgba[..., :3] = small
    rgba[..., 3] = 1.0
    return rgba.reshape(-1)


class CockpitWindow:
    """The Dear PyGui cockpit window. Build once with :meth:`setup`, then :meth:`update` +
    :meth:`render_frame` per loop; :meth:`running` reports the viewport state."""

    def __init__(self, *, title: str = "Binaural Console - cockpit") -> None:
        self.title = title
        self._fonts: dict[str, int | str] = {}
        self._tex = "cam_tex"
        self._tex_w, self._tex_h = 470, 300  # matches the camera panel aspect (CAM_W×CAM_H)
        self._canvas = "cockpit_canvas"
        self._win = "cockpit_win"
        self._host = "canvas_host"  # auto-filling child; its measured size drives the fit-scale
        self._src_cb = "ck_src"     # the test-source checkbox, so engine state can sync it
        self._cushion_sl = "ck_cushion"  # the cushion slider, so the live value can seed it
        self._mono_px = 36          # the mono atlas size (see _load_fonts); text is measured at it
        self._k = 1.0  # live fit-scale: design canvas → current window (set each frame)
        # Test-source state the view owns, so a radar click/drag moves the source live even in the
        # preview (and the live app reads it back). Placed by clicking the radar; dragged to move.
        self._test_on = False
        self._test_az = 60.0
        self._solo = False
        self._rotate = False
        self._predict = True   # orientation prediction (cancels camera staleness) — on by default
        # hooks the live app overrides (no-ops in the preview)
        self.on_method = lambda method: None
        self.on_backend = lambda backend: None
        self.on_cushion = lambda ms: None
        self.on_predict = lambda on: None
        self.on_exclusive = lambda on: None
        self.on_test = lambda on: None        # movable test source on/off (the console's T)
        self.on_solo = lambda on: None        # hear only the test source (O)
        self.on_rotate = lambda on: None      # auto-rotate the test source (R)
        self.on_place_test = lambda az: None  # placed/dragged on the radar → world azimuth (deg)
        self.on_recenter = lambda: None
        self.on_quit = lambda: None

    # -------------------------------------------------- build
    def setup(self) -> None:
        import dearpygui.dearpygui as dpg

        # Declare the process DPI-aware BEFORE Dear PyGui starts, so Windows stops virtualising
        # the window on a scaled (e.g. 150%) display and GetDpiForSystem reports the true DPI.
        # Otherwise Windows reports 96 while DPG scales drawing by the real ratio, so the canvas
        # renders DPI× too big and clips. Harmless if already set / on an older OS.
        try:
            import ctypes
            # -4 = DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:  # noqa: BLE001
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor (Win 8.1+)
            except Exception:  # noqa: BLE001
                pass

        dpg.create_context()
        self._load_fonts(dpg)
        self._apply_theme(dpg)

        import numpy as np
        blank = np.zeros(self._tex_w * self._tex_h * 4, np.float32).tolist()
        with dpg.texture_registry():
            dpg.add_dynamic_texture(self._tex_w, self._tex_h, blank, tag=self._tex)

        with dpg.window(tag=self._win, no_scrollbar=True):
            # The drawlist lives in a child that auto-fills the window above the controls strip.
            # Each frame the fit-scale is read from this child's MEASURED size (get_item_rect_size),
            # which is in Dear PyGui's own draw-coordinate space — the same space draw_* uses — so
            # the scale is correct regardless of the OS/HiDPI pixel ratio (mixing the viewport's
            # client pixels with item sizes otherwise overflows the window on a 150% display).
            with dpg.child_window(tag=self._host, border=False, width=-1, height=-CONTROLS_H):
                dpg.add_drawlist(width=CANVAS_W, height=CANVAS_H, tag=self._canvas)
                # DPG 2.x dropped per-call `font=` on draw_text; a drawlist's text follows the font
                # bound to the drawlist item. Bind the mono face (the data cockpit's primary type).
                if self._fonts.get("mono"):
                    try:
                        dpg.bind_item_font(self._canvas, self._fonts["mono"])
                    except Exception:  # noqa: BLE001 — a failed bind just leaves the default font
                        pass
            # Two rows so the controls fit a narrow window instead of running off the right edge.
            with dpg.group(horizontal=True):   # row 1 — the render engine
                dpg.add_text("method")
                dpg.add_combo(
                    ("nearest", "timealign", "minphase", "notchwarp"),
                    default_value="nearest", width=120,
                    callback=lambda s, a: self.on_method(a))
                dpg.add_spacer(width=8)
                dpg.add_text("backend")
                dpg.add_radio_button(
                    ("cpu", "rust", "gpu"), default_value="rust", horizontal=True,
                    callback=lambda s, a: self.on_backend(a))
                dpg.add_spacer(width=8)
                dpg.add_text("cushion")
                dpg.add_slider_int(
                    tag=self._cushion_sl, default_value=60, min_value=20, max_value=120, width=120,
                    format="%d ms", callback=lambda s, a: self.on_cushion(float(a)))
            with dpg.group(horizontal=True):   # row 2 — prediction, the test source, actions
                dpg.add_checkbox(label="predict", default_value=True,
                                 callback=lambda s, a: self._set_predict(bool(a)))
                dpg.add_checkbox(label="exclusive",
                                 callback=lambda s, a: self.on_exclusive(bool(a)))
                dpg.add_spacer(width=12)
                # Test-source experiment layer (the console's T/O/R): a movable virtual sound for
                # A/B-ing localization. Click the radar to place it, drag to move it continuously.
                tst = dpg.add_text("test:")
                with dpg.tooltip(tst):
                    dpg.add_text("A movable virtual test sound for A/B-ing localization.\n"
                                 "Click the radar to place it; drag to move it continuously.\n"
                                 "source = on/off · solo = hear only it (mute the mix) · "
                                 "orbit = auto-rotate it around you.")
                dpg.add_checkbox(label="source", tag=self._src_cb,
                                 callback=lambda s, a: self._set_test(bool(a)))
                dpg.add_checkbox(label="solo", callback=lambda s, a: self._set_solo(bool(a)))
                dpg.add_checkbox(label="orbit", callback=lambda s, a: self._set_rotate(bool(a)))
                dpg.add_spacer(width=12)
                dpg.add_button(label="Recenter", callback=lambda: self.on_recenter())
                dpg.add_button(label="Quit", callback=lambda: self.on_quit())
        dpg.set_primary_window(self._win, True)

        # Height leaves room below the canvas for the controls row + titlebar, with a margin so a
        # high-DPI host (where the OS window is smaller in content px) doesn't clip the controls.
        # min size keeps the two controls rows on screen; the content scales to whatever is left.
        dpg.create_viewport(title=self.title, width=CANVAS_W + 28, height=CANVAS_H + 160,
                            min_width=720, min_height=520, clear_color=_c(M.BG))
        # Click / drag the radar to place and move the test source (the console's click-to-place).
        with dpg.handler_registry():
            dpg.add_mouse_click_handler(button=dpg.mvMouseButton_Left,
                                        callback=lambda s, a: self._place_from_mouse(dpg))
            dpg.add_mouse_drag_handler(button=dpg.mvMouseButton_Left,
                                       callback=lambda s, a: self._place_from_mouse(dpg))

        dpg.setup_dearpygui()
        dpg.show_viewport()
        if self._fonts.get("sans"):
            dpg.bind_font(self._fonts["sans"])

    def _set_test(self, on: bool) -> None:
        self._test_on = on
        self.on_test(on)

    def _set_solo(self, on: bool) -> None:
        self._solo = on
        self.on_solo(on)

    def _set_rotate(self, on: bool) -> None:
        self._rotate = on
        self.on_rotate(on)

    def _set_predict(self, on: bool) -> None:
        self._predict = on
        self.on_predict(on)

    def set_cushion_display(self, ms: float) -> None:
        """Seed the cushion slider with the engine's actual launch value (it isn't the default)."""
        try:
            import dearpygui.dearpygui as dpg
            if dpg.does_item_exist(self._cushion_sl):
                dpg.set_value(self._cushion_sl, int(round(ms)))
        except Exception:  # noqa: BLE001
            pass

    def set_test_state(self, on: bool, az: float) -> None:
        """Reflect the live engine's test-source state on the radar + the 'source' checkbox (so a
        key/other UI change, or the engine's auto-rotation, keeps the view in sync)."""
        self._test_on = bool(on)
        self._test_az = float(az)
        try:
            import dearpygui.dearpygui as dpg
            if dpg.does_item_exist(self._src_cb):
                dpg.set_value(self._src_cb, bool(on))
        except Exception:  # noqa: BLE001 — the checkbox is a convenience mirror, never critical
            pass

    def _radar_az_at(self, dx, dy):
        """World azimuth (deg) for a DESIGN-space point on the radar, else None (inverse of
        ``azimuth_to_radar_xy``: x = cx − R·sinβ, y = cy − R·cosβ). A little past the ring still
        tracks, only the dead-centre is ignored — so a drag keeps following the cursor."""
        cx, cy = RAD_X + RAD_W / 2, RAD_Y + RAD_H / 2 + 6
        r = math.hypot(dx - cx, dy - cy)
        if r < 6 or r > (RAD_W / 2 - 34) * 1.6:
            return None
        return math.degrees(math.atan2(cx - dx, cy - dy))

    def _place_from_mouse(self, dpg) -> None:
        try:
            mx, my = dpg.get_drawing_mouse_pos()  # drawlist-local coords (0..canvas)
        except Exception:  # noqa: BLE001 — not over the drawlist
            return
        k = self._k or 1.0
        az = self._radar_az_at(mx / k, my / k)  # back to design space
        if az is not None:
            self._test_on = True
            self._test_az = float(az)
            self.on_test(True)
            self.on_place_test(float(az))

    def _load_fonts(self, dpg) -> None:
        faces = {
            "display": ("Rajdhani-Bold.ttf", 34),
            "display_sm": ("Rajdhani-SemiBold.ttf", 15),
            "mono": ("IBMPlexMono-Medium.ttf", self._mono_px),  # canvas atlas; large → crisp scaled
            "mono_sm": ("IBMPlexMono-Regular.ttf", 12),
            "sans": ("IBMPlexSans-var.ttf", 15),
        }
        with dpg.font_registry():
            for key, (fname, size) in faces.items():
                path = _FONT_DIR / fname
                if path.exists():
                    try:
                        self._fonts[key] = dpg.add_font(_dpg_font_path(path), size)
                    except Exception:  # noqa: BLE001 — a bad face just falls back to the default
                        pass

    def _apply_theme(self, dpg) -> None:
        with dpg.theme() as theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_WindowBg, _c(M.BG))
                dpg.add_theme_color(dpg.mvThemeCol_Text, _c(M.FG))
                dpg.add_theme_color(dpg.mvThemeCol_Button, _c(M.PANEL2))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, _c(M.LINE))
                dpg.add_theme_color(dpg.mvThemeCol_FrameBg, _c(M.PANEL2))
                dpg.add_theme_color(dpg.mvThemeCol_CheckMark, _c(M.ACCENT))
                dpg.add_theme_color(dpg.mvThemeCol_SliderGrab, _c(M.ACCENT))
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 5)
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 10, 8)
        dpg.bind_theme(theme)

    # -------------------------------------------------- per-frame
    def running(self) -> bool:
        import dearpygui.dearpygui as dpg
        return dpg.is_dearpygui_running()

    def render_frame(self) -> None:
        import dearpygui.dearpygui as dpg
        dpg.render_dearpygui_frame()

    def teardown(self) -> None:
        import dearpygui.dearpygui as dpg
        dpg.destroy_context()

    def update(self, view: M.CockpitView, *, camera_rgb=None,
               world_az=None, channels=None, facing_i=0, head_deg=0.0, source_az=None) -> None:
        """Repaint the whole canvas from ``view``, scaled to the current window size."""
        import dearpygui.dearpygui as dpg

        if camera_rgb is not None:
            try:
                dpg.set_value(self._tex, _frame_to_texture(camera_rgb, self._tex_w, self._tex_h))
            except Exception:  # noqa: BLE001 — a bad frame should never kill the GUI loop
                pass

        # Fit the fixed design canvas (CANVAS_W×CANVAS_H) into the host child with ONE uniform
        # scale — so the radar's circles stay circular. The process is DPI-aware (see setup), so
        # the host's measured size and the drawlist's 1:1 drawing are in the same pixel space:
        # k = host / design fills the window correctly at any size and DPI.
        try:
            aw, ah = dpg.get_item_rect_size(self._host)
        except Exception:  # noqa: BLE001 — before first layout, assume the design size
            aw, ah = CANVAS_W, CANVAS_H
        if aw < 10 or ah < 10:  # not laid out yet on the first frame
            aw, ah = CANVAS_W, CANVAS_H
        avail_w = max(320.0, aw - 2 * MARGIN)
        avail_h = max(220.0, ah - 2 * MARGIN)
        self._k = max(0.35, min(avail_w / CANVAS_W, avail_h / CANVAS_H, 6.0))
        dpg.configure_item(self._canvas, width=int(CANVAS_W * self._k),
                           height=int(CANVAS_H * self._k))

        dpg.delete_item(self._canvas, children_only=True)
        self._panel(dpg, CAM_X, CAM_Y, CAM_W, CAM_H, title=None)
        self._img(dpg, self._tex, CAM_X, CAM_Y, CAM_W, CAM_H)
        self._panel(dpg, RAD_X, RAD_Y, RAD_W, RAD_H, title="WORLD-LOCKED RING")
        if world_az is not None and channels is not None:
            src = self._test_az if self._test_on else source_az  # radar marker follows the test src
            self._radar(dpg, world_az, channels, facing_i, head_deg, src)
        self._lag_strip(dpg, view)
        self._lag_panel(dpg, view)

    # -------------------------------------------------- scaled draw primitives
    # Each takes DESIGN coordinates/sizes and multiplies by self._k before drawing, so the whole
    # cockpit scales with the window from one place. Colours are RGBA tuples (converted via _c).
    def _rect(self, dpg, x, y, w, h, *, fill=None, color=None, rounding=0.0, thickness=1.0):
        k = self._k
        kw = {"parent": self._canvas, "thickness": max(1.0, thickness * k)}
        if fill is not None:
            kw["fill"] = _c(fill)
        if color is not None:
            kw["color"] = _c(color)
        if rounding:
            kw["rounding"] = rounding * k
        dpg.draw_rectangle((x * k, y * k), ((x + w) * k, (y + h) * k), **kw)

    def _circle(self, dpg, cx, cy, r, *, fill=None, color=None, thickness=1.0):
        k = self._k
        kw = {"parent": self._canvas, "thickness": max(1.0, thickness * k)}
        if fill is not None:
            kw["fill"] = _c(fill)
        if color is not None:
            kw["color"] = _c(color)
        dpg.draw_circle((cx * k, cy * k), r * k, **kw)

    def _line(self, dpg, x1, y1, x2, y2, *, color, thickness=1.0):
        k = self._k
        dpg.draw_line((x1 * k, y1 * k), (x2 * k, y2 * k), color=_c(color),
                      thickness=max(1.0, thickness * k), parent=self._canvas)

    def _dashed(self, dpg, x1, y1, x2, y2, color, *, dash=5.0, gap=4.0, thickness=1.0):
        """A dashed line in design coords (each segment scaled by self._k via `_line`)."""
        dist = math.hypot(x2 - x1, y2 - y1)
        if dist < 1.0:
            return
        ux, uy, step = (x2 - x1) / dist, (y2 - y1) / dist, dash + gap
        s = 0.0
        while s < dist:
            e = min(s + dash, dist)
            self._line(dpg, x1 + ux * s, y1 + uy * s, x1 + ux * e, y1 + uy * e,
                       color=color, thickness=thickness)
            s += step

    def _poly(self, dpg, pts, *, fill=None, color=None):
        k = self._k
        sp = [(px * k, py * k) for px, py in pts]
        kw = {"parent": self._canvas}
        if fill is not None:
            kw["fill"] = _c(fill)
        if color is not None:
            kw["color"] = _c(color)
        dpg.draw_polygon(sp, **kw)

    def _img(self, dpg, tex, x, y, w, h):
        k = self._k
        dpg.draw_image(tex, (x * k, y * k), ((x + w) * k, (y + h) * k), parent=self._canvas)

    def _text(self, dpg, x, y, s, color, size=14, font="mono"):  # noqa: ARG002 — font reserved
        # `font` is kept in the signature (call sites name a face) but NOT forwarded: DPG 2.x
        # draw_text has no per-call font — the drawlist's bound font is used. Position and size
        # scale by self._k so text grows with the window.
        k = self._k
        dpg.draw_text((x * k, y * k), s, color=_c(color), size=max(6.0, size * k),
                      parent=self._canvas)

    def _text_size(self, dpg, s, size):
        """Exact (width, height) of `s` in DESIGN coords at design `size`, from Dear PyGui's own
        glyph metrics (`get_text_size`) for the bound mono atlas — scaled from the atlas size to
        `size`. No hand-tuned widths; guards to a monospace estimate if measuring fails."""
        try:
            f = self._fonts.get("mono")
            m = dpg.get_text_size(s, font=f) if f else dpg.get_text_size(s)
            if m and m[0] and m[0] > 0:
                r = size / self._mono_px
                return m[0] * r, m[1] * r
        except Exception:  # noqa: BLE001 — measuring may be unavailable before the atlas is built
            pass
        return len(s) * 0.6 * size, size  # fallback estimate

    def _ctext(self, dpg, px, py, s, color, size=11, anchor="c"):
        """Text aligned on (px, py) using the measured bounding box, so labels are exactly centred
        and L/R-symmetric. anchor: 'c' centre on px, 'l' left edge at px, 'r' right edge at px."""
        w, h = self._text_size(dpg, s, size)
        x = px - w / 2 if anchor == "c" else px - w if anchor == "r" else px
        self._text(dpg, x, py - h / 2, s, color, size)

    # -------------------------------------------------- drawing helpers
    def _panel(self, dpg, x, y, w, h, *, title):
        self._rect(dpg, x, y, w, h, fill=M.PANEL, color=M.LINE, rounding=8)
        if title:
            self._text(dpg, x + 10, y + 7, title, M.ACCENT, 13, "display_sm")

    def _radar(self, dpg, world_az, channels, facing_i, head_deg, source_az):
        cx, cy = RAD_X + RAD_W / 2, RAD_Y + RAD_H / 2 + 6
        R = RAD_W / 2 - 50   # leave room OUTSIDE the ring for the per-speaker labels + cardinals
        for f in (1.0, 0.66, 0.33):
            self._circle(dpg, cx, cy, R * f, color=M.LINE)
        # 0°/180° (vertical) and ±90° (horizontal) azimuth axes — a dashed reference cross, so the
        # cardinal directions are explicit lines, not just words.
        self._dashed(dpg, cx, cy - R - 7, cx, cy + R + 7, M.FAINT, dash=4, gap=5)
        self._dashed(dpg, cx - R - 7, cy, cx + R + 7, cy, M.FAINT, dash=4, gap=5)
        # gaze cone
        poly = [(cx, cy)]
        for d in range(-28, 29, 4):
            poly.append(azimuth_to_radar_xy(head_deg + d, R, cx, cy))
        self._poly(dpg, poly, color=(*M.ACCENT[:3], 0), fill=(*M.ACCENT[:3], 36))
        # cardinal words: FRONT/BACK further out on the vertical axis (clear of C's label), LEFT/
        # RIGHT on the horizontal axis. Centred/edge-aligned from the measured box → symmetric.
        self._ctext(dpg, cx, cy - R - 30, "FRONT", M.FAINT, 11, "c")
        self._ctext(dpg, cx, cy + R + 30, "BACK", M.FAINT, 11, "c")
        self._ctext(dpg, cx - R - 14, cy, "LEFT", M.FAINT, 11, "r")
        self._ctext(dpg, cx + R + 14, cy, "RIGHT", M.FAINT, 11, "l")
        # speakers: a dashed spoke from the centre to each dot, and a channel+angle label placed
        # just OUTSIDE the dot (radially outward) — so left labels sit left of their dot and right
        # labels right of theirs, a true left↔right mirror, each centred on the measured box.
        for i, ch in enumerate(channels):
            if ch == "LFE":
                continue  # non-directional (sits at az 0, atop C) — not an azimuth-radar speaker
            az = float(world_az[i])
            x, y = azimuth_to_radar_xy(az, R, cx, cy)
            on = i == facing_i
            col = M.ACTIVE if on else M.ACCENT
            self._dashed(dpg, cx, cy, x, y, col if on else M.LINE, dash=5, gap=4)
            if on:
                self._circle(dpg, x, y, 12, color=M.ACTIVE)
            self._circle(dpg, x, y, 7 if on else 5, color=M.PANEL, fill=col)
            lab = f"{ch} {az:+.0f}°" if abs(az) > 0.5 else f"{ch} 0°"
            lx, ly = azimuth_to_radar_xy(az, R + 16, cx, cy)  # radially just outside the dot
            self._ctext(dpg, lx, ly, lab, M.ACTIVE if on else M.MUTED, 11, "c")
        if source_az is not None:
            sx, sy = azimuth_to_radar_xy(float(source_az), R, cx, cy)
            self._line(dpg, cx, cy, sx, sy, color=M.GOOD)
            self._circle(dpg, sx, sy, 6, fill=M.GOOD, color=M.GOOD)
        # head arrow
        base = [(0, -12), (7, 7), (0, 3), (-7, 7)]
        r = math.radians(-head_deg)
        cc, ss = math.cos(r), math.sin(r)
        arr = [(cx + px * cc - py * ss, cy + px * ss + py * cc) for px, py in base]
        self._poly(dpg, arr, color=M.FG, fill=M.FG)

    def _lag_strip(self, dpg, view: M.CockpitView):
        # the narrow right strip: tracker + engine config at a glance
        x, y = LAG_X, LAG_Y
        self._panel(dpg, x, y, LAG_W, LAG_H, title="STATUS")
        yy = y + 32

        def line(s, color, size=12, font="mono_sm", gap=15):
            nonlocal yy
            self._text(dpg, x + 10, yy, s, color, size, font)
            yy += gap

        line("tracker", M.MUTED)
        for seg in _wrap(view.tracker_text, 24):   # wrap so "… · 640×480@30" never spills the box
            line(seg, M.FG)
        yy += 8
        line("engine", M.MUTED)
        for seg in _wrap(view.render_label, 24):
            line(seg, M.FG)
        yy += 10
        line("dropouts", M.MUTED)
        for seg in _wrap(view.dropouts_text, 24):
            line(seg, view.dropouts_color, size=13, font="mono")
        yy += 8
        for r in view.reasons:
            for seg in _wrap(r, 24):
                line(seg, M.FAINT, size=11, gap=13)
            yy += 4

    def _lag_panel(self, dpg, view: M.CockpitView):
        # width 988 so the panel's right edge (12+988=1000) lines up with the STATUS strip above.
        x, y, w, h = 12, PANEL_Y, 988, CANVAS_H - PANEL_Y - 8
        self._panel(dpg, x, y, w, h, title="LATENCY · HEAD-MOVE → EAR")
        # headline number + verdict pill
        self._text(dpg, x + 14, y + 26, view.headline_ms, M.FG, 40, "display")
        self._text(dpg, x + 92, y + 58, "ms p50" if view.measured else "ms est", M.MUTED, 13,
                   "mono_sm")
        pill = view.verdict
        self._rect(dpg, x + 170, y + 34, 14 + len(pill) * 9, 20, fill=view.verdict_color,
                   rounding=10)
        self._text(dpg, x + 178, y + 37, pill, M.BG, 13, "display_sm")
        self._text(dpg, x + 14, y + 72, view.detail, M.FAINT, 12, "mono_sm")
        # component waterfall
        comp_x, comp_y, comp_w = x + 300, y + 30, 360
        if view.components:
            total = sum(c.ms for c in view.components) or 1.0
            cxp = comp_x
            for c in view.components:
                seg = comp_w * c.ms / total
                self._rect(dpg, cxp, comp_y, seg, 18, fill=c.color, color=M.BG)
                self._text(dpg, cxp + 4, comp_y + 2, f"{c.label} {c.ms:.0f}", M.BG, 11, "mono_sm")
                cxp += seg
            self._text(dpg, comp_x, comp_y + 24,
                       "stale(camera) · queue(FIFO) · device(WASAPI)", M.FAINT, 11, "mono_sm")
        # histogram (kept clear of the readout strip at the panel's bottom)
        hx, hy, hw, hh = x + 300, y + 78, 360, h - 128
        self._histogram(dpg, hx, hy, hw, hh, view)
        # bars (right)
        bx, by = x + 700, y + 30
        for bar in view.bars:
            self._text(dpg, bx, by, bar.label, M.MUTED, 12, "mono_sm")
            self._meter(dpg, bx + 90, by + 1, 180, 10, bar.frac, bar.color, peak=bar.peak)
            self._text(dpg, bx, by + 16, bar.text, M.FG, 12, "mono_sm")
            by += 42
        # readout chips along the bottom of the panel (inside it, padded off the border)
        rx = x + 14
        ry = y + h - 20
        for r in view.readouts:
            self._text(dpg, rx, ry, r.label, M.MUTED, 12, "mono_sm")
            rx += 10 + len(r.label) * 8
            self._text(dpg, rx, ry, r.value, r.color, 13, "mono")
            rx += 22 + len(r.value) * 9

    def _histogram(self, dpg, x, y, w, h, view: M.CockpitView):
        n = len(view.histogram) or 1
        mx = max(view.histogram) or 1
        bw = w / n
        for i, c in enumerate(view.histogram):
            bh = (c / mx) * (h - 14)
            self._rect(dpg, x + i * bw, y + (h - 14) - bh, max(1.0, bw - 1), bh,
                       fill=M.ACCENT, color=M.ACCENT)
        for frac, lbl in ((0.0, "0"), (0.5, f"{view.hist_hi_ms/2:.0f}"),
                          (1.0, f"{view.hist_hi_ms:.0f} ms")):
            self._text(dpg, x + frac * w - 8, y + h - 12, lbl, M.FAINT, 11, "mono_sm")

    def _meter(self, dpg, x, y, w, h, frac, color, *, peak=None):
        frac = max(0.0, min(1.0, frac))
        self._rect(dpg, x, y, w, h, fill=M.PANEL2, rounding=4)
        if frac > 0:
            self._rect(dpg, x, y, w * frac, h, fill=color, rounding=4)
        if peak is not None:
            px = x + w * max(0.0, min(1.0, peak))
            self._line(dpg, px, y - 2, px, y + h + 2, color=M.FG)

def _wrap(s: str, width: int) -> list[str]:
    out, cur = [], ""
    for wd in s.split():
        if len(cur) + len(wd) + 1 > width:
            out.append(cur)
            cur = wd
        else:
            cur = f"{cur} {wd}".strip()
    if cur:
        out.append(cur)
    return out or [s]


# ------------------------------------------------------------------ preview

def run_preview() -> int:
    """Launch the cockpit with a synthetic, animated scene — no camera, no audio.

    Slice-1 way to judge the real native look on the host before the live engine is wired in
    (slice 2). Returns 0 on a clean quit, 1 if Dear PyGui is unavailable."""
    if not available():
        print("Dear PyGui is not installed — run `py -3.12 -m pip install dearpygui` "
              "(or `uv sync --extra gui`).")
        return 1

    import time

    import numpy as np

    from binaural_headtracker.diagnostics import classify
    from binaural_headtracker.radar import nearest_front_channel

    channels = ["FL", "FR", "C", "LFE", "SL", "SR", "RL", "RR"]
    world_az = [30.0, -30.0, 0.0, 0.0, 110.0, -110.0, 150.0, -150.0]
    rng = np.random.default_rng(0)

    win = CockpitWindow()
    quit_flag = {"q": False}
    win.on_quit = lambda: quit_flag.__setitem__("q", True)
    win.setup()
    win.set_test_state(True, 60.0)  # preview: show the test source so the radar marker is visible

    t0 = tprev = time.monotonic()
    try:
        while win.running() and not quit_flag["q"]:
            now = time.monotonic()
            t, dt = now - t0, now - tprev
            tprev = now
            if win._rotate and win._test_on:  # "orbit" checkbox demo: sweep the test source
                win._test_az = (win._test_az + 40.0 * dt + 180.0) % 360.0 - 180.0
            yaw = 25.0 * math.sin(t * 0.4)
            head_deg = -yaw
            dirs = [[a - yaw, 0.0] for a in world_az]
            facing_i = nearest_front_channel(dirs)
            # Predict on → orientation prediction cancels most of the camera staleness, so the
            # `stale` term and the whole head→ear latency drop; off → the full staleness shows.
            # This is what the live engine does (B8.6); the preview mirrors it so the toggle reads.
            stale = 8.0 if win._predict else 22.0
            centre = (stale + 13.0 + 13.0 + 4.0) + 4.0 * math.sin(t * 0.7)  # +queue+device+camera
            samples = (rng.gamma(12.0, centre / 12.0, 240)).tolist()
            p50, p95, p99 = (float(x) for x in np.percentile(samples, (50, 95, 99)))
            load = 0.38 + 0.08 * math.sin(t * 1.3)
            diag = classify(
                block_size=512, samplerate=48000, render_ms=load * 10.7,
                render_ms_peak=(load + 0.25) * 10.7, fifo_fill_blocks=6.0, cushion_blocks=6,
                output_latency_ms=13.0, input_latency_ms=10.7, pose_rate_hz=58.0,
                measured_pose_age_ms=p50 - 13.0, measured_pose_age_p95_ms=p95 - 13.0,
                measured_pose_age_p99_ms=p99 - 13.0, measured_pose_age_n=len(samples),
                measured_stale_ms=stale, measured_queue_ms=13.0, measured_device_ms=13.0,
                detect_ms=11.0, camera_fps=30.0, cam_w=640, cam_h=480,
                render_label="rust · nearest (8-bus)")
            view = M.build_view(diag, latency_samples=samples, rate_hz=58.0, method="nearest",
                                backend="rust", predict_ms=(p50 if win._predict else None),
                                facing_label=channels[facing_i])
            # a synthetic camera frame (a drifting gradient + a face-ish blob)
            cam = _synthetic_frame(t)
            win.update(view, camera_rgb=cam, world_az=world_az, channels=channels,
                       facing_i=facing_i, head_deg=head_deg)  # test src from the view (click radar)
            win.render_frame()
    finally:
        win.teardown()
    return 0


def _synthetic_frame(t: float):
    import numpy as np
    h, w = 180, 320
    yy, xx = np.mgrid[0:h, 0:w]
    r = (28 + 10 * np.sin(xx / 40 + t)).astype(np.uint8)
    g = (36 + 10 * np.cos(yy / 30 - t)).astype(np.uint8)
    b = (54 + 8 * np.sin((xx + yy) / 50)).astype(np.uint8)
    frame = np.dstack([r, g, b])
    cx = int(w / 2 - 50 * math.sin(t * 0.4))
    frame[h // 2 - 40:h // 2 + 40, cx - 30:cx + 30] = (60, 120, 130)
    return frame


def main(argv=None) -> int:  # noqa: ARG001 — entry point for `py -3.12 apps/cockpit_view.py`
    return run_preview()


if __name__ == "__main__":
    raise SystemExit(main())
