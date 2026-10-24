"""Host-side console render harness (B6.8 evidence). Runs on Windows py.exe (has cv2+PIL).

argv: <out_prefix> <src_dir> <apps_dir>  (all Windows paths). Renders representative console
frames to <out_prefix>_stats.png and <out_prefix>_clean.png with no camera/audio — compose()
is pure drawing.
"""
import sys
from types import SimpleNamespace

out_prefix, src_dir, apps_dir = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path[:0] = [apps_dir, src_dir]

import numpy as np  # noqa: E402
import cv2  # noqa: E402
import _console as C  # noqa: E402
from binaural_headtracker.diagnostics import classify  # noqa: E402


def synth_frame(w=960, h=720):
    """A webcam-like frame: vertical gradient + mild noise + a face-ish oval, so the overlays
    (face box, HUD) have something plausible underneath."""
    yy = np.linspace(20, 60, h).astype(np.uint8)
    frame = np.stack([np.tile(yy[:, None], (1, w))] * 3, axis=-1).astype(np.uint8)
    frame += (np.random.default_rng(7).integers(0, 10, (h, w, 3))).astype(np.uint8)
    cv2.ellipse(frame, (w // 2, h // 2), (150, 200), 0, 0, 360, (90, 110, 130), -1, cv2.LINE_AA)
    return frame


channels = ["FL", "FR", "FC", "LFE", "SL", "SR", "RL", "RR"]
world_az = [30.0, -30.0, 0.0, 0.0, 110.0, -110.0, 150.0, -150.0]
pose = SimpleNamespace(yaw=12.3, pitch=-4.1, roll=1.8)
readout = [("rate", "60Hz", C.GOOD), ("neutral", "set", C.GOOD), ("facing", "FC", C.ACTIVE)]
buttons = [("Recenter", "C", True), ("Stats", "S", False), ("Quit", "Q", False)]

diag = classify(
    block_size=512, samplerate=48000, render_ms=6.1, render_ms_peak=8.9,
    render_ms_test=1.2, fifo_fill_blocks=2.0, cushion_blocks=3,
    render_label="rust · timealign · grid 2deg · 7.1+test", output_latency_ms=10.0,
    input_latency_ms=4.0, pose_rate_hz=60.0, recent_underruns=0, recent_overflows=0,
    measured_pose_age_ms=74.0, measured_pose_age_p95_ms=96.0, measured_pose_age_p99_ms=112.0,
    measured_pose_age_n=240, measured_stale_ms=28.0, measured_queue_ms=24.0,
    measured_device_ms=22.0, detect_ms=3.1, camera_fps=30.0, cam_w=960, cam_h=720)

common = dict(title="world-lock passthrough — webcam 0", live="TRACKING", live_color=C.GOOD,
              pose=pose, world_az=world_az, channels=channels, facing_i=2, head_deg=12.3,
              source_az=-70.0, readout=readout, buttons=buttons, method="timealign",
              rate_hz=60.0, phase=0.25)

canvas = C.compose(synth_frame(), diagnosis=diag, show_stats=True, audio_stats=True, **common)
cv2.imwrite(out_prefix + "_stats.png", canvas)
canvas = C.compose(synth_frame(), diagnosis=diag, show_stats=False, audio_stats=True, **common)
cv2.imwrite(out_prefix + "_clean.png", canvas)
_t = getattr(C, "_type", None)
print("wrote", out_prefix + "_stats.png", out_prefix + "_clean.png",
      "ttf=", (_t.available() if _t else False))
