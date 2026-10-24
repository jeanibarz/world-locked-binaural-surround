"""Web cockpit — serves the owner's redesign (``apps/web_cockpit/``) over a dependency-free local
HTTP server and drives it from the same engine + view-model as the Dear PyGui cockpit.

Transport (all stdlib, so the UI runs in any browser and is inspectable):
  * ``GET /``, static files  — the web_cockpit/ assets.
  * ``GET /events``  (SSE)   — the engine pushes one state JSON per tick (+ toasts).
  * ``GET /camera``  (MJPEG) — the live camera, shown by ``<img src="/camera">``.
  * ``POST /api/<name>``     — a control event; the body is a JSON arg list dispatched to the
    matching ``on_*`` engine hook (the same hooks the DPG window uses).
  * ``GET  /api/get_config`` — the dropdown options + current values for the settings dialog.

``WebCockpit`` has the SAME method surface as the DPG ``CockpitWindow`` (``setup`` / ``running``
/ ``update`` / ``render_frame`` / ``toast`` / ``set_*_display`` / ``on_*`` / ``teardown``), so the
engine's capture loop drives it as a drop-in — the HTTP server runs in background threads while the
loop stays on the main thread.

``view_to_state(diag, view, **extras)`` is the pure view-model → JSON mapping (unit-testable).
"""
from __future__ import annotations

import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_WEB_DIR = Path(__file__).resolve().parent / "web_cockpit"
_CTYPE = {".html": "text/html", ".css": "text/css", ".js": "application/javascript",
          ".png": "image/png", ".svg": "image/svg+xml", ".ico": "image/x-icon"}

_SEG_STYLE = {
    "capture": {"capture": True},
    "stale": {"bg": "#ff6b6b", "fg": "#2a0a0a"},
    "queue": {"bg": "#ffb340", "fg": "#2b1a02"},
    "device": {"bg": "#35d0d0", "fg": "#06222a"},
}
_VERDICT_LEVEL = {"GOOD": "good", "OK": "good", "AT RISK": "warn", "RISK": "warn",
                  "FAILING": "bad", "FAIL": "bad", "BAD": "bad"}


def _deg(x):
    return "—" if x is None else f"{x:+.0f}°"


def view_to_state(diag, view, *, pose=None, rate_hz=0.0, cushion_ms=0.0, exclusive=False,
                  hrir_name="", comp="", hp_eq=True, predict_on=False, cam_fps=0.0,
                  cam_w=0, cam_h=0, face_locked=False, facing_label="", head_deg=0.0,
                  cal=None) -> dict:
    """Map one engine tick (a ``Diagnosis`` + its ``CockpitView`` + a little loop state) to the
    cockpit.js state dict. Pure; safe to unit-test."""
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

    rate_dot = "#4cd98a" if rate_hz >= 20 else "#ffb340"
    hrir_val = " · ".join(x for x in (hrir_name, comp.upper() if comp else "",
                                      "HP EQ" if hp_eq else "") if x)
    health = [
        {"label": "Tracker rate", "dot": rate_dot,
         "value": f"{rate_hz:.0f} Hz (cam {cam_fps:.0f})"},
        {"label": "Detect time", "value": f"{diag.detect_ms:.0f} ms", "dot": "#4cd98a"},
        {"label": "Backend", "value": view.backend, "dot": "#4cd98a"},
        {"label": "Rendering", "value": view.method, "dot": "#4cd98a"},
        {"label": "Cushion", "dot": "#5b6678",
         "value": f"{diag.cushion_blocks} blk · {cushion_ms:.0f} ms"
                  f" · {'exclusive' if exclusive else 'shared'}"},
        {"label": "HRIR", "dot": "#5b6678", "value": hrir_val},
    ]

    hname = getattr(getattr(diag, "health", None), "name", "OK").upper()
    hlvl = "bad" if "FAIL" in hname else ("warn" if "RISK" in hname else "good")
    render_load = {
        "pct": round(max(0.0, min(1.0, diag.render_load)) * 100),
        "peak_pct": round(max(0.0, min(1.0, diag.render_load_peak)) * 100),
        "level": hlvl,
        "text": f"{diag.render_load * 100:.0f}% (peak {diag.render_load_peak * 100:.0f}%)"
                f" · {diag.block_ms:.1f} ms/blk",
    }
    buf_frac = (diag.fifo_fill_blocks / (2 * diag.cushion_blocks)) if diag.cushion_blocks else 0.0
    buf_ok = diag.fifo_fill_blocks >= max(1.0, diag.cushion_blocks - 0.5)
    buffer = {
        "pct": round(max(0.0, min(1.0, buf_frac)) * 100),
        "level": "good" if buf_ok else "warn",
        "text": f"{diag.fifo_fill_blocks:.0f} / {diag.cushion_blocks} blk"
                f" · {'steady' if buf_ok else 'low'}",
    }
    dropped = diag.recent_underruns + diag.recent_overflows
    dropouts = {"text": f"{diag.recent_overflows} over · {diag.recent_underruns} under",
                "pct": min(100, dropped * 20), "color": "#ff6b6b" if dropped else "#4cd98a"}

    state = {
        "cam_res": (f"{cam_w}×{cam_h} @ {cam_fps:.0f}" if cam_w else ""),
        "face_locked": bool(face_locked),
        "face_badge": "Face locked" if face_locked else "Searching…",
        "health": health,
        "predict": bool(predict_on),
        "facing": f"Facing {facing_label}" if facing_label else "",
        "pose": None,
        "render_load": render_load, "buffer": buffer, "dropouts": dropouts,
        "latency": {
            "ms": view.headline_ms,
            "detail": ("median · " + view.detail) if view.measured else view.detail,
            "verdict": view.verdict,
            "verdict_level": _VERDICT_LEVEL.get(view.verdict.upper(), "good"),
            "segments": segments, "bins": bins, "p50_pct": round(p50_pct, 1),
        },
    }
    if pose is not None:
        # Show the calibration-RELATIVE orientation (not the raw head pose) so Recenter visibly
        # re-zeros the tiles and the radar. yaw comes from head_deg (the cal-relative azimuth the
        # engine already derives); pitch/roll are the sign-applied deltas from the neutral cal.
        if cal is not None:
            rel_pitch = cal.pitch_sign * (getattr(pose, "pitch", 0.0) - cal.pitch0)
            rel_roll = cal.roll_sign * (getattr(pose, "roll", 0.0) - cal.roll0)
        else:
            rel_pitch, rel_roll = getattr(pose, "pitch", 0.0), getattr(pose, "roll", 0.0)
        state["pose"] = {"yaw": _deg(head_deg), "pitch": _deg(rel_pitch), "roll": _deg(rel_roll),
                         "yaw_deg": float(head_deg)}
    return state


class _Handler(BaseHTTPRequestHandler):
    wc: WebCockpit = None  # set by the factory

    def log_message(self, *a):  # silence the default stderr access log
        pass

    def _send(self, code, ctype, body: bytes, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/events":
            return self._sse()
        if path == "/camera":
            return self._mjpeg()
        if path == "/api/get_config":
            return self._send(200, "application/json", json.dumps(self.wc.config()).encode())
        # static file
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        fp = (_WEB_DIR / rel).resolve()
        if _WEB_DIR not in fp.parents and fp != _WEB_DIR or not fp.is_file():
            return self._send(404, "text/plain", b"not found")
        self._send(200, _CTYPE.get(fp.suffix, "application/octet-stream"), fp.read_bytes())

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if not path.startswith("/api/"):
            return self._send(404, "text/plain", b"not found")
        name = path[len("/api/"):]
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"[]"
        try:
            args = json.loads(raw or b"[]")
        except Exception:  # noqa: BLE001
            args = []
        if not isinstance(args, list):
            args = [args]
        self.wc.dispatch(name, args)
        self._send(200, "application/json", b"{}")

    def _sse(self):
        q = self.wc._subscribe()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            while not self.wc._stop:
                try:
                    msg = q.get(timeout=1.0)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                self.wfile.write(b"data: " + msg.encode() + b"\n\n")
                self.wfile.flush()
        except Exception:  # noqa: BLE001 — client went away
            pass
        finally:
            self.wc._unsubscribe(q)

    def _mjpeg(self):
        import time as _t
        try:
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            last = None
            while not self.wc._stop:
                frame = self.wc._frame
                if frame is not None and frame is not last:
                    last = frame
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n"
                                     b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n")
                    self.wfile.write(frame)
                    self.wfile.write(b"\r\n")
                    self.wfile.flush()
                _t.sleep(0.02)
        except Exception:  # noqa: BLE001
            pass


class WebCockpit:
    """Drop-in for the DPG ``CockpitWindow``: serves the web UI and is driven by the engine loop."""

    def __init__(self, *, title: str = "world-lock passthrough",
                 width: int = 1296, height: int = 836,
                 port: int = 0, open_browser: bool = True):
        self.title, self._w, self._h, self._port = title, width, height, port
        self._open_browser = open_browser
        self._srv = None
        self._stop = False
        self._restart = False
        self._frame = None                   # latest JPEG bytes (MJPEG)
        self._subs: list[queue.Queue] = []   # SSE client queues
        self._subs_lock = threading.Lock()
        self._cameras: list[tuple[str, int]] = []
        self._hrir_names: list[str] = []
        self._config: dict = {}
        self.url = ""
        for name in ("predict", "recenter", "method", "backend", "block", "cushion", "exclusive",
                     "hrir", "hrir_blind", "comp", "camera", "cam_format", "place_test", "test",
                     "solo", "rotate", "quit", "loc_start", "loc_answer", "loc_stop",
                     "blind_result", "reset_defaults",
                     "settings_begin", "settings_commit", "settings_cancel"):
            setattr(self, "on_" + name, (lambda *a, **k: None))

    # ---- seed ----
    def set_cameras(self, cams):
        self._cameras = list(cams)

    def set_hrir_sets(self, names):
        self._hrir_names = list(names)

    def config(self):
        return {"cameras": self._cameras, "hrir": self._hrir_names,
                "methods": ["nearest", "timealign", "minphase", "notchwarp"],
                "resolutions": ["default", "320×240", "640×480", "848×480", "1280×720"],
                "framerates": ["default", "30 fps", "60 fps"], "samplerate": 48000, **self._config}

    # ---- display setters (feed the config the settings dialog seeds from) ----
    def set_block_display(self, n):
        self._config["block"] = int(n)

    def set_cushion_display(self, ms):
        self._config["cushion_ms"] = float(ms)

    def set_backend_display(self, b):
        self._config["backend"] = str(b)

    def set_hrir_display(self, n):
        self._config["hrir_name"] = str(n)

    def set_comp_display(self, c):
        self._config["comp"] = str(c)

    def set_camera_display(self, i):
        self._config["camera"] = int(i)

    def set_method_display(self, m):
        self._config["method"] = str(m)

    def set_exclusive_display(self, on):
        self._config["exclusive"] = bool(on)

    def set_hpeq_display(self, on):
        self._config["hp_eq"] = bool(on)

    def set_test_state(self, on, az):
        pass

    def set_loc_status(self, text):
        self._broadcast({"type": "drawer_status", "text": text})

    def set_loc_active(self, on):
        pass

    # ---- control dispatch (POST /api/<name>) ----
    def _close(self):
        self.on_quit()
        self.request_stop()

    def dispatch(self, name: str, args: list):
        """Route a POST /api/<name> control event to the matching engine hook. Unknown or
        intentionally-inert names (ready, set_drawer_tab, blind_play, settings_apply, …) no-op."""
        table = {
            "set_predict": lambda: self.on_predict(bool(args[0])),
            "recenter": self.on_recenter,
            "set_method": lambda: self.on_method(str(args[0])),
            "set_backend": lambda: self.on_backend(str(args[0])),
            "set_block": lambda: self.on_block(int(args[0])),
            "set_cushion": lambda: self.on_cushion(float(args[0])),
            "set_exclusive": lambda: self.on_exclusive(bool(args[0])),
            "set_hrir": lambda: self.on_hrir(str(args[0])),
            "set_comp": lambda: self.on_comp(str(args[0])),
            "set_camera": lambda: self.on_camera(int(args[0])),
            "set_cam_format": lambda: self.on_cam_format(
                (int(args[0]), int(args[1]), int(args[2]))),
            "place_test": lambda: self.on_place_test(float(args[0])),
            "set_test": lambda: self.on_test(bool(args[0])),
            "set_solo": lambda: self.on_solo(bool(args[0])),
            "set_rotate": lambda: self.on_rotate(bool(args[0])),
            "reset_defaults": self.on_reset_defaults,
            "settings_begin": self.on_settings_begin,    # dialog opened: snapshot + defer saving
            "settings_commit": self.on_settings_commit,  # Confirm: persist the previewed settings
            "settings_cancel": self.on_settings_cancel,  # Cancel/close: restore the snapshot
            "blind_shuffle_start": lambda: self.toast(
                "blind A/B - coming to its own panel", "info"),
            "window_close": self._close,
        }
        fn = table.get(name)
        if fn is None:
            return
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - a bad control event must not crash the server
            self.toast(f"control error: {exc}", "error")

    # ---- SSE plumbing ----
    def _subscribe(self):
        q = queue.Queue(maxsize=4)
        with self._subs_lock:
            self._subs.append(q)
        return q

    def _unsubscribe(self, q):
        with self._subs_lock:
            if q in self._subs:
                self._subs.remove(q)

    def _broadcast(self, obj):
        msg = json.dumps(obj)
        with self._subs_lock:
            for q in self._subs:
                try:
                    if q.full():
                        q.get_nowait()
                    q.put_nowait(msg)
                except Exception:  # noqa: BLE001
                    pass

    # ---- lifecycle ----
    def setup(self):
        self._srv = ThreadingHTTPServer(("127.0.0.1", self._port), self._handler_cls())
        self._port = self._srv.server_address[1]
        self.url = f"http://127.0.0.1:{self._port}/index.html"
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        print(f"[web cockpit] serving {self.url}", flush=True)
        if self._open_browser:
            self._launch_browser()

    def _handler_cls(self):
        wc = self
        return type("_BoundHandler", (_Handler,), {"wc": wc})

    def _launch_browser(self):
        import shutil
        import subprocess
        import sys
        chrome = None
        for c in ("chrome", "google-chrome",
                  r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                  r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"):
            if shutil.which(c) or Path(c).exists():
                chrome = c
                break
        try:
            if chrome:
                subprocess.Popen([chrome, f"--app={self.url}",
                                  f"--window-size={self._w},{self._h}"])
            else:
                import webbrowser
                webbrowser.open(self.url)
        except Exception:  # noqa: BLE001
            print(f"[web cockpit] open this in a browser: {self.url}", file=sys.stderr, flush=True)

    def running(self):
        return not self._stop

    def request_stop(self, *, restart=False):
        self._restart = restart or self._restart
        self._stop = True

    def teardown(self):
        self.request_stop()
        try:
            if self._srv:
                self._srv.shutdown()
        except Exception:  # noqa: BLE001
            pass

    # ---- per-frame push (engine loop, main thread) ----
    def update(self, view, *, camera_rgb=None, landmarks=None, pose=None, world_az=None,
               channels=None, facing_i=0, head_deg=0.0, source_az=None, diag=None,
               rate_hz=0.0, cushion_ms=0.0, predict=False, exclusive=False, cam_fps=0.0,
               cam_w=0, cam_h=0, hrir_name="", comp="", cal=None, **_ignored):
        if diag is None:
            return
        facing = ""
        if channels is not None and 0 <= facing_i < len(channels):
            facing = channels[facing_i]
        state = view_to_state(
            diag, view, pose=pose, rate_hz=rate_hz, cushion_ms=cushion_ms, exclusive=exclusive,
            hrir_name=hrir_name, comp=comp, predict_on=predict, cam_fps=cam_fps, cam_w=cam_w,
            cam_h=cam_h, face_locked=(pose is not None), facing_label=facing,
            head_deg=head_deg, cal=cal)
        self._broadcast({"type": "state", "state": state})
        if camera_rgb is not None:
            self._encode_frame(camera_rgb, landmarks)

    def _encode_frame(self, rgb, landmarks=None):
        try:
            import cv2
            bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            if landmarks is not None:
                h, w = bgr.shape[:2]
                for lm in landmarks:
                    # the display frame is mirrored; landmarks are in the un-mirrored space
                    x, y = int((1.0 - lm.x) * w), int(lm.y * h)
                    if 0 <= x < w and 0 <= y < h:
                        cv2.circle(bgr, (x, y), 1, (208, 208, 53), -1)  # cyan #35d0d0 (BGR)
            ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
            if ok:
                self._frame = buf.tobytes()
        except Exception:  # noqa: BLE001
            pass

    def render_frame(self):  # parity with the DPG window; web pushes in update()
        pass

    def toast(self, message, kind="error", *, seconds=6.0):
        self._broadcast({"type": "toast", "message": str(message), "kind": kind})
