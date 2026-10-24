"""Web cockpit — a pywebview front-end that renders the owner's redesign
(``apps/web_cockpit/``) and is driven by the same engine + view-model as the Dear PyGui
cockpit (``binaural_headtracker.cockpit``).

Two halves:
  * ``view_to_state(diag, view, **extras)`` — pure function: maps a ``Diagnosis`` + its
    ``CockpitView`` (+ a little loop state) to the JSON shape ``cockpit.js`` expects. Pure and
    unit-testable (no pywebview, no GUI).
  * ``WebCockpit`` — owns the pywebview window, exposes ``Api`` to the page
    (``window.pywebview.api.*`` → the ``on_*`` hooks the engine sets, exactly like the DPG window),
    and pushes state/camera frames to the page via ``evaluate_js``.

pywebview's ``webview.start()`` owns the main thread, so the capture loop runs as its worker
(``WebCockpit.run(worker)``); ``update()``/``push_frame()`` are called from that worker thread and
``evaluate_js`` is safe to call across threads.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

_WEB_DIR = Path(__file__).resolve().parent / "web_cockpit"

# latency segment styling keyed by the view-model's component label, matching the design exactly
# (capture = striped/estimated; the rest are the measured parts).
_SEG_STYLE = {
    "capture": {"capture": True},
    "stale": {"bg": "#ff6b6b", "fg": "#2a0a0a"},
    "queue": {"bg": "#ffb340", "fg": "#2b1a02"},
    "device": {"bg": "#35d0d0", "fg": "#06222a"},
}
_VERDICT_LEVEL = {  # view.verdict string -> css level class
    "GOOD": "good", "OK": "good", "AT RISK": "warn", "RISK": "warn",
    "FAILING": "bad", "FAIL": "bad", "BAD": "bad",
}


def _deg(x: float | None) -> str:
    if x is None:
        return "—"
    return f"{x:+.0f}°"


def view_to_state(diag, view, *, pose=None, rate_hz=0.0, cushion_ms=0.0, exclusive=False,
                  hrir_name="", comp="", hp_eq=True, predict_on=False, cam_fps=0.0,
                  cam_w=0, cam_h=0, face_locked=False, facing_label="") -> dict:
    """Map one engine tick to the cockpit.js state dict. Pure; safe to unit-test."""
    # --- latency bar segments (design colors, by component label) + histogram ---
    segments = []
    for c in view.components:
        style = dict(_SEG_STYLE.get(c.label, {"bg": "#5b6678", "fg": "#06222a"}))
        label = ("~" if c.label == "capture" else "") + f"{c.ms:.0f}"
        segments.append({"ms": max(c.ms, 0.0), "label": label, **style})

    hist = tuple(view.histogram)
    mx = max(hist) if hist else 1
    bins = [round(v / mx * 72.0, 1) for v in hist] if mx else [0] * len(hist)
    lo, hi = view.hist_lo_ms, view.hist_hi_ms
    span = (hi - lo) or 1.0
    p50_pct = max(0.0, min(100.0, (diag.end_to_end_ms - lo) / span * 100.0))

    # --- SYSTEM health rows (the design's six) ---
    rate_dot = "#4cd98a" if rate_hz >= 20 else "#ffb340"
    health = [
        {"label": "Tracker rate", "value": f"{rate_hz:.0f} Hz (cam {cam_fps:.0f})", "dot": rate_dot},
        {"label": "Detect time", "value": f"{diag.detect_ms:.0f} ms", "dot": "#4cd98a"},
        {"label": "Backend", "value": view.backend, "dot": "#4cd98a"},
        {"label": "Rendering", "value": view.method, "dot": "#4cd98a"},
        {"label": "Cushion", "value": f"{cushion_ms:.0f} ms · {'exclusive' if exclusive else 'shared'}", "dot": "#5b6678"},
        {"label": "HRIR", "value": " · ".join(x for x in (hrir_name, comp.upper() if comp else "", "HP EQ" if hp_eq else "") if x), "dot": "#5b6678"},
    ]

    # --- meters (render load, output buffer, dropouts) straight from the Diagnosis ---
    hname = getattr(getattr(diag, "health", None), "name", "OK").upper()
    hlvl = "bad" if "FAIL" in hname else ("warn" if "RISK" in hname else "good")
    render_load = {
        "pct": round(max(0.0, min(1.0, diag.render_load)) * 100),
        "peak_pct": round(max(0.0, min(1.0, diag.render_load_peak)) * 100),
        "level": hlvl,
        "text": f"{diag.render_load * 100:.0f}% (peak {diag.render_load_peak * 100:.0f}%) · {diag.block_ms:.1f} ms/blk",
    }
    buf_frac = (diag.fifo_fill_blocks / (2 * diag.cushion_blocks)) if diag.cushion_blocks else 0.0
    buf_ok = diag.fifo_fill_blocks >= max(1.0, diag.cushion_blocks - 0.5)
    buffer = {
        "pct": round(max(0.0, min(1.0, buf_frac)) * 100),
        "level": "good" if buf_ok else "warn",
        "text": f"{diag.fifo_fill_blocks:.0f} / {diag.cushion_blocks} blk · {'steady' if buf_ok else 'low'}",
    }
    dropped = diag.recent_underruns + diag.recent_overflows
    dropouts = {
        "text": f"{diag.recent_overflows} over · {diag.recent_underruns} under",
        "pct": min(100, dropped * 20),
        "color": "#ff6b6b" if dropped else "#4cd98a",
    }

    state = {
        "cam_res": (f"{cam_w}×{cam_h} @ {cam_fps:.0f}" if cam_w else ""),
        "face_locked": bool(face_locked),
        "face_badge": "Face locked" if face_locked else "Searching…",
        "health": health,
        "predict": bool(predict_on),
        "facing": f"Facing {facing_label}" if facing_label else "",
        "pose": None,
        "latency": {
            "ms": view.headline_ms,
            "detail": "median · " + view.detail if view.measured else view.detail,
            "verdict": view.verdict,
            "verdict_level": _VERDICT_LEVEL.get(view.verdict.upper(), "good"),
            "segments": segments,
            "bins": bins,
            "p50_pct": round(p50_pct, 1),
        },
    }
    if render_load:
        state["render_load"] = render_load
    if buffer:
        state["buffer"] = buffer
    state["dropouts"] = dropouts
    if pose is not None:
        yaw, pitch, roll = getattr(pose, "yaw", 0.0), getattr(pose, "pitch", 0.0), getattr(pose, "roll", 0.0)
        state["pose"] = {"yaw": _deg(yaw), "pitch": _deg(pitch), "roll": _deg(roll), "yaw_deg": float(yaw)}
    return state


class _Api:
    """Exposed to the page as ``window.pywebview.api``. Each method forwards to the matching engine
    hook on the owning WebCockpit (the same ``on_*`` callbacks the DPG window uses)."""

    def __init__(self, wc: "WebCockpit") -> None:
        self._wc = wc

    def ready(self):            self._wc._ready = True
    def set_predict(self, on):  self._wc.on_predict(bool(on))
    def recenter(self):         self._wc.on_recenter()
    def set_method(self, m):    self._wc.on_method(str(m))
    def set_backend(self, b):   self._wc.on_backend(str(b))
    def set_block(self, n):     self._wc.on_block(int(n))
    def set_cushion(self, ms):  self._wc.on_cushion(float(ms))
    def set_exclusive(self, on): self._wc.on_exclusive(bool(on))
    def set_hrir(self, name):   self._wc.on_hrir(str(name))
    def set_comp(self, c):      self._wc.on_comp(str(c))
    def set_camera(self, i):    self._wc.on_camera(int(i))
    def set_cam_format(self, w, h, fps): self._wc.on_cam_format((int(w), int(h), int(fps)))
    def place_test(self, az):   self._wc.on_place_test(float(az))
    def set_test(self, on):     self._wc.on_test(bool(on))
    def set_solo(self, on):     self._wc.on_solo(bool(on))
    def set_rotate(self, on):   self._wc.on_rotate(bool(on))
    def open_settings(self):    self._wc.open_settings()
    def settings_apply(self):   self._wc.on_settings_apply()
    def set_drawer_tab(self, t): self._wc.on_drawer_tab(str(t))
    def blind_play(self, x):    self._wc.on_blind_play(str(x))
    def blind_shuffle_start(self): self._wc.on_blind_shuffle_start()
    def window_min(self):       self._wc._win and self._wc._win.minimize()
    def window_max(self):       self._wc._win and self._wc._win.toggle_fullscreen()
    def window_close(self):     self._wc.on_quit()
    # settings seed: the page asks for the current options/values on load
    def get_config(self):       return self._wc.config()


class WebCockpit:
    """pywebview window presenting ``apps/web_cockpit/index.html``, driven by the engine loop."""

    def __init__(self, *, title: str = "world-lock passthrough", width: int = 1296, height: int = 836):
        self.title, self._w, self._h = title, width, height
        self._win = None
        self._api = _Api(self)
        self._ready = False
        self._stop = False
        self._restart = False
        self._cameras: list[tuple[str, int]] = []
        self._hrir_names: list[str] = []
        self._config: dict = {}
        # no-op hooks (the engine overrides these, same contract as the DPG CockpitWindow)
        for name in ("predict", "recenter", "method", "backend", "block", "cushion", "exclusive",
                     "hrir", "comp", "camera", "cam_format", "place_test", "test", "solo", "rotate",
                     "quit", "drawer_tab", "blind_play", "blind_shuffle_start", "settings_apply"):
            setattr(self, "on_" + name, (lambda *a, **k: None))

    # ---- seed (before run) ----
    def set_cameras(self, cams):     self._cameras = list(cams)
    def set_hrir_sets(self, names):  self._hrir_names = list(names)
    def set_config(self, cfg):       self._config = dict(cfg)
    def config(self):                return {"cameras": self._cameras, "hrir": self._hrir_names, **self._config}

    # ---- display setters (state push carries the truth; these update the seed for the next push) ----
    def set_block_display(self, n):   self._config["block"] = int(n)
    def set_cushion_display(self, ms): self._config["cushion_ms"] = float(ms)
    def set_backend_display(self, b):  self._config["backend"] = str(b)
    def set_hrir_display(self, n):     self._config["hrir_name"] = str(n)
    def set_comp_display(self, c):     self._config["comp"] = str(c)
    def set_camera_display(self, i):   self._config["camera"] = int(i)
    def set_test_state(self, on, az):  self._config["test_on"], self._config["test_az"] = bool(on), float(az)
    def set_loc_status(self, text):    self._eval(f"window.cockpit&&window.cockpit.setDrawerStatus&&window.cockpit.setDrawerStatus({json.dumps(text)})")
    def set_loc_active(self, on):      pass

    # ---- run model ----
    def run(self, worker):
        """Create the window and start pywebview (blocks on the main thread). ``worker`` runs the
        capture loop in a pywebview-managed thread. Returns True if a reload was requested."""
        import webview
        self._win = webview.create_window(
            self.title, url=str(_WEB_DIR / "index.html"), js_api=self._api,
            width=self._w, height=self._h, background_color="#0d1117")
        self._win.events.closed += self._on_closed
        webview.start(worker, gui="edgechromium" if _is_windows() else None, private_mode=False)
        return self._restart

    def _on_closed(self):
        self._stop = True

    def running(self) -> bool:
        return not self._stop

    def quit_requested(self) -> bool:
        return self._stop

    def request_stop(self, *, restart: bool = False):
        self._restart = restart or self._restart
        self._stop = True
        try:
            if self._win:
                self._win.destroy()
        except Exception:  # noqa: BLE001
            pass

    def on_quit(self):
        self.request_stop()

    def open_settings(self):
        """Open the settings dialog as a second pywebview window."""
        import webview
        try:
            webview.create_window("Settings", url=str(_WEB_DIR / "settings.html"),
                                  js_api=self._api, width=576, height=1012, background_color="#0d1117")
        except Exception:  # noqa: BLE001
            self.toast("could not open settings", "error")

    def on_drawer_tab(self, _tab):
        pass

    # ---- per-frame push (called from the worker thread) ----
    def _eval(self, code: str):
        try:
            if self._win is not None and self._ready:
                self._win.evaluate_js(code)
        except Exception:  # noqa: BLE001 — a dropped frame must never crash the loop
            pass

    def update(self, state: dict):
        self._eval(f"window.cockpit&&window.cockpit.applyState({json.dumps(state)})")

    def push_frame(self, rgb):
        """Encode an HxWx3 uint8 RGB array to a JPEG data URL and push it to the camera panel."""
        try:
            import cv2
            bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
            if not ok:
                return
            b64 = base64.b64encode(buf.tobytes()).decode("ascii")
            self._eval(f'window.cockpit&&window.cockpit.setCameraFrame("data:image/jpeg;base64,{b64}")')
        except Exception:  # noqa: BLE001
            pass

    def render_frame(self):  # parity with the DPG window; web pushes in update()
        pass

    def toast(self, message: str, kind: str = "error", *, seconds: float = 6.0):
        self._eval(f"window.cockpit&&window.cockpit.toast&&window.cockpit.toast({json.dumps(message)},{json.dumps(kind)})")

    def wait_ready(self, timeout_s: float = 10.0):
        import time
        t0 = time.time()
        while not self._ready and not self._stop and time.time() - t0 < timeout_s:
            time.sleep(0.02)

    def teardown(self):
        self.request_stop()


def _is_windows() -> bool:
    import sys
    return sys.platform.startswith("win")
