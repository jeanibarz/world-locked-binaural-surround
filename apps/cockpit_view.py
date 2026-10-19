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
CANVAS_W, CANVAS_H = 1012, 452
CAM_X, CAM_Y, CAM_W, CAM_H = 12, 12, 516, 300
RAD_X, RAD_Y, RAD_W, RAD_H = 540, 12, 300, 300
LAG_X, LAG_Y, LAG_W, LAG_H = 852, 12, 148, 300  # narrow strip; full lag panel is below the cam
PANEL_Y = 324  # the wide latency panel under the camera/radar


def available() -> bool:
    """True if Dear PyGui can be imported (host with the ``gui`` extra); False on CI/WSL."""
    try:
        import dearpygui.dearpygui  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _c(rgba: M.RGBA) -> list[int]:
    return [int(v) for v in rgba]


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

    def __init__(self, *, title: str = "Binaural Console — cockpit") -> None:
        self.title = title
        self._fonts: dict[str, int | str] = {}
        self._tex = "cam_tex"
        self._tex_w, self._tex_h = 516, 300
        self._canvas = "cockpit_canvas"
        self._win = "cockpit_win"
        # hooks the live app overrides (no-ops in the preview)
        self.on_method = lambda method: None
        self.on_backend = lambda backend: None
        self.on_cushion = lambda ms: None
        self.on_predict = lambda on: None
        self.on_exclusive = lambda on: None
        self.on_recenter = lambda: None
        self.on_quit = lambda: None

    # -------------------------------------------------- build
    def setup(self) -> None:
        import dearpygui.dearpygui as dpg

        dpg.create_context()
        self._load_fonts(dpg)
        self._apply_theme(dpg)

        import numpy as np
        blank = np.zeros(self._tex_w * self._tex_h * 4, np.float32).tolist()
        with dpg.texture_registry():
            dpg.add_dynamic_texture(self._tex_w, self._tex_h, blank, tag=self._tex)

        with dpg.window(tag=self._win, no_scrollbar=True):
            dpg.add_drawlist(width=CANVAS_W, height=CANVAS_H, tag=self._canvas)
            with dpg.group(horizontal=True):
                dpg.add_text("method")
                dpg.add_combo(
                    ("nearest", "timealign", "minphase", "notchwarp"),
                    default_value="nearest", width=130,
                    callback=lambda s, a: self.on_method(a))
                dpg.add_spacer(width=10)
                dpg.add_text("backend")
                dpg.add_radio_button(
                    ("cpu", "rust", "gpu"), default_value="rust", horizontal=True,
                    callback=lambda s, a: self.on_backend(a))
                dpg.add_spacer(width=10)
                dpg.add_text("cushion")
                dpg.add_slider_int(
                    default_value=60, min_value=20, max_value=120, width=150, format="%d ms",
                    callback=lambda s, a: self.on_cushion(float(a)))
                dpg.add_spacer(width=10)
                dpg.add_checkbox(label="predict", default_value=True,
                                 callback=lambda s, a: self.on_predict(bool(a)))
                dpg.add_checkbox(label="exclusive",
                                 callback=lambda s, a: self.on_exclusive(bool(a)))
                dpg.add_spacer(width=10)
                dpg.add_button(label="Recenter", callback=lambda: self.on_recenter())
                dpg.add_button(label="Quit", callback=lambda: self.on_quit())
        dpg.set_primary_window(self._win, True)

        dpg.create_viewport(title=self.title, width=CANVAS_W + 24, height=CANVAS_H + 86,
                            clear_color=_c(M.BG))
        dpg.setup_dearpygui()
        dpg.show_viewport()
        if self._fonts.get("sans"):
            dpg.bind_font(self._fonts["sans"])

    def _load_fonts(self, dpg) -> None:
        faces = {
            "display": ("Rajdhani-Bold.ttf", 34),
            "display_sm": ("Rajdhani-SemiBold.ttf", 15),
            "mono": ("IBMPlexMono-Medium.ttf", 14),
            "mono_sm": ("IBMPlexMono-Regular.ttf", 12),
            "sans": ("IBMPlexSans-var.ttf", 15),
        }
        with dpg.font_registry():
            for key, (fname, size) in faces.items():
                path = _FONT_DIR / fname
                if path.exists():
                    try:
                        self._fonts[key] = dpg.add_font(str(path), size)
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
        """Repaint the whole canvas from ``view`` (and the optional live state for the radar)."""
        import dearpygui.dearpygui as dpg

        if camera_rgb is not None:
            try:
                dpg.set_value(self._tex, _frame_to_texture(camera_rgb, self._tex_w, self._tex_h))
            except Exception:  # noqa: BLE001 — a bad frame should never kill the GUI loop
                pass

        cv = self._canvas
        dpg.delete_item(cv, children_only=True)
        self._panel(dpg, CAM_X, CAM_Y, CAM_W, CAM_H, title=None)
        dpg.draw_image(self._tex, (CAM_X, CAM_Y), (CAM_X + CAM_W, CAM_Y + CAM_H), parent=cv)
        self._panel(dpg, RAD_X, RAD_Y, RAD_W, RAD_H, title="WORLD-LOCKED RING")
        if world_az is not None and channels is not None:
            self._radar(dpg, world_az, channels, facing_i, head_deg, source_az)
        self._lag_strip(dpg, view)
        self._lag_panel(dpg, view)
        self._readouts(dpg, view)

    # -------------------------------------------------- drawing helpers
    def _panel(self, dpg, x, y, w, h, *, title):
        cv = self._canvas
        dpg.draw_rectangle((x, y), (x + w, y + h), fill=_c(M.PANEL), color=_c(M.LINE),
                           rounding=8, parent=cv)
        if title:
            dpg.draw_text((x + 10, y + 7), title, color=_c(M.ACCENT), size=13,
                          font=self._fonts.get("display_sm"), parent=cv)

    def _text(self, dpg, x, y, s, color, size=14, font="mono"):
        dpg.draw_text((x, y), s, color=_c(color), size=size,
                      font=self._fonts.get(font), parent=self._canvas)

    def _radar(self, dpg, world_az, channels, facing_i, head_deg, source_az):
        cv = self._canvas
        cx, cy = RAD_X + RAD_W / 2, RAD_Y + RAD_H / 2 + 6
        R = RAD_W / 2 - 34
        for f in (1.0, 0.66, 0.33):
            dpg.draw_circle((cx, cy), R * f, color=_c(M.LINE), parent=cv)
        # gaze cone
        poly = [(cx, cy)]
        for d in range(-28, 29, 4):
            poly.append(azimuth_to_radar_xy(head_deg + d, R, cx, cy))
        dpg.draw_polygon(poly, color=(*M.ACCENT[:3], 0), fill=(*M.ACCENT[:3], 36), parent=cv)
        for lbl, az in (("FRONT", 0), ("LEFT", 90), ("BACK", 180), ("RIGHT", -90)):
            x, y = azimuth_to_radar_xy(float(az), R + 16, cx, cy)
            dpg.draw_text((x - 16, y - 7), lbl, color=_c(M.MUTED), size=12,
                          font=self._fonts.get("display_sm"), parent=cv)
        for i, ch in enumerate(channels):
            x, y = azimuth_to_radar_xy(float(world_az[i]), R, cx, cy)
            on = i == facing_i
            col = M.ACTIVE if on else M.ACCENT
            if on:
                dpg.draw_circle((x, y), 12, color=_c(M.ACTIVE), parent=cv)
            dpg.draw_circle((x, y), 7 if on else 5, color=_c(M.PANEL), fill=_c(col), parent=cv)
            tx, ty = azimuth_to_radar_xy(float(world_az[i]), R - 18, cx, cy)
            self._text(dpg, tx - 8, ty - 7, ch, M.ACTIVE if on else M.MUTED, 12, "mono_sm")
        if source_az is not None:
            sx, sy = azimuth_to_radar_xy(float(source_az), R, cx, cy)
            dpg.draw_line((cx, cy), (sx, sy), color=_c(M.GOOD), parent=cv)
            dpg.draw_circle((sx, sy), 6, fill=_c(M.GOOD), color=_c(M.GOOD), parent=cv)
        # head arrow
        base = [(0, -12), (7, 7), (0, 3), (-7, 7)]
        r = math.radians(-head_deg)
        cc, ss = math.cos(r), math.sin(r)
        arr = [(cx + px * cc - py * ss, cy + px * ss + py * cc) for px, py in base]
        dpg.draw_polygon(arr, color=_c(M.FG), fill=_c(M.FG), parent=cv)

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
        line(view.tracker_text, M.FG, gap=24)
        line("engine", M.MUTED)
        for seg in _wrap(view.render_label, 18):
            line(seg, M.FG)
        yy += 10
        line("dropouts", M.MUTED)
        line(view.dropouts_text, view.dropouts_color, size=13, font="mono", gap=24)
        for r in view.reasons:
            for seg in _wrap(r, 18):
                line(seg, M.FAINT, size=11, gap=13)
            yy += 4

    def _lag_panel(self, dpg, view: M.CockpitView):
        cv = self._canvas
        x, y, w, h = 12, PANEL_Y, 1000, CANVAS_H - PANEL_Y - 8
        self._panel(dpg, x, y, w, h, title="LATENCY · HEAD-MOVE → EAR")
        # headline number + verdict pill
        self._text(dpg, x + 14, y + 26, view.headline_ms, M.FG, 40, "display")
        self._text(dpg, x + 92, y + 58, "ms p50" if view.measured else "ms est", M.MUTED, 13,
                   "mono_sm")
        pill = view.verdict
        dpg.draw_rectangle((x + 170, y + 34), (x + 170 + 14 + len(pill) * 9, y + 54),
                           fill=_c(view.verdict_color), rounding=10, parent=cv)
        self._text(dpg, x + 178, y + 37, pill, M.BG, 13, "display_sm")
        self._text(dpg, x + 14, y + 72, view.detail, M.FAINT, 12, "mono_sm")
        # component waterfall
        comp_x, comp_y, comp_w = x + 300, y + 30, 360
        if view.components:
            total = sum(c.ms for c in view.components) or 1.0
            cxp = comp_x
            for c in view.components:
                seg = comp_w * c.ms / total
                dpg.draw_rectangle((cxp, comp_y), (cxp + seg, comp_y + 18),
                                   fill=_c(c.color), color=_c(M.BG), parent=cv)
                self._text(dpg, cxp + 4, comp_y + 2, f"{c.label} {c.ms:.0f}", M.BG, 11, "mono_sm")
                cxp += seg
            self._text(dpg, comp_x, comp_y + 24,
                       "stale(camera) · queue(FIFO) · device(WASAPI)", M.FAINT, 11, "mono_sm")
        # histogram
        hx, hy, hw, hh = x + 300, y + 78, 360, h - 96
        self._histogram(dpg, hx, hy, hw, hh, view)
        # bars (right)
        bx, by = x + 700, y + 30
        for bar in view.bars:
            self._text(dpg, bx, by, bar.label, M.MUTED, 12, "mono_sm")
            self._meter(dpg, bx + 90, by + 1, 180, 10, bar.frac, bar.color, peak=bar.peak)
            self._text(dpg, bx, by + 16, bar.text, M.FG, 12, "mono_sm")
            by += 42

    def _histogram(self, dpg, x, y, w, h, view: M.CockpitView):
        cv = self._canvas
        n = len(view.histogram) or 1
        mx = max(view.histogram) or 1
        bw = w / n
        for i, c in enumerate(view.histogram):
            bh = (c / mx) * (h - 14)
            dpg.draw_rectangle((x + i * bw, y + (h - 14) - bh), (x + (i + 1) * bw - 1, y + h - 14),
                               fill=_c(M.ACCENT), color=_c(M.ACCENT), parent=cv)
        for frac, lbl in ((0.0, "0"), (0.5, f"{view.hist_hi_ms/2:.0f}"),
                          (1.0, f"{view.hist_hi_ms:.0f} ms")):
            self._text(dpg, x + frac * w - 8, y + h - 12, lbl, M.FAINT, 11, "mono_sm")

    def _meter(self, dpg, x, y, w, h, frac, color, *, peak=None):
        cv = self._canvas
        frac = max(0.0, min(1.0, frac))
        dpg.draw_rectangle((x, y), (x + w, y + h), fill=_c(M.PANEL2), rounding=4, parent=cv)
        if frac > 0:
            dpg.draw_rectangle((x, y), (x + w * frac, y + h), fill=_c(color), rounding=4,
                               parent=cv)
        if peak is not None:
            px = x + w * max(0.0, min(1.0, peak))
            dpg.draw_line((px, y - 2), (px, y + h + 2), color=_c(M.FG), parent=cv)

    def _readouts(self, dpg, view: M.CockpitView):
        x = 12
        y = CANVAS_H - 2  # a thin strip at the very bottom of the canvas
        for r in view.readouts:
            self._text(dpg, x, y - 14, r.label, M.MUTED, 12, "mono_sm")
            x += 8 + len(r.label) * 7
            self._text(dpg, x, y - 14, r.value, r.color, 13, "mono")
            x += 18 + len(r.value) * 8


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

    t0 = time.monotonic()
    try:
        while win.running() and not quit_flag["q"]:
            t = time.monotonic() - t0
            yaw = 25.0 * math.sin(t * 0.4)
            head_deg = -yaw
            dirs = [[a - yaw, 0.0] for a in world_az]
            facing_i = nearest_front_channel(dirs)
            # a latency distribution wandering around ~48 ms
            centre = 46.0 + 4.0 * math.sin(t * 0.7)
            samples = (rng.gamma(9.0, centre / 9.0, 240)).tolist()
            p50, p95, p99 = (float(x) for x in np.percentile(samples, (50, 95, 99)))
            load = 0.38 + 0.08 * math.sin(t * 1.3)
            diag = classify(
                block_size=512, samplerate=48000, render_ms=load * 10.7,
                render_ms_peak=(load + 0.25) * 10.7, fifo_fill_blocks=6.0, cushion_blocks=6,
                output_latency_ms=13.0, input_latency_ms=10.7, pose_rate_hz=58.0,
                measured_pose_age_ms=p50 - 13.0, measured_pose_age_p95_ms=p95 - 13.0,
                measured_pose_age_p99_ms=p99 - 13.0, measured_pose_age_n=len(samples),
                measured_stale_ms=22.0, measured_queue_ms=13.0, measured_device_ms=13.0,
                detect_ms=11.0, camera_fps=30.0, cam_w=640, cam_h=480,
                render_label="rust · nearest (8-bus)")
            view = M.build_view(diag, latency_samples=samples, rate_hz=58.0, method="nearest",
                                backend="rust", predict_ms=p50, facing_label=channels[facing_i])
            # a synthetic camera frame (a drifting gradient + a face-ish blob)
            cam = _synthetic_frame(t)
            win.update(view, camera_rgb=cam, world_az=world_az, channels=channels,
                       facing_i=facing_i, head_deg=head_deg, source_az=60.0)
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
