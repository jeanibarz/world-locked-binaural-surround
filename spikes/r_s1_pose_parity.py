"""R-S1 — pose-parity spike (RFC: standalone-distributable-and-rust-engine).

The make-or-break gate for the zero-Python north star: can a **Rust-ownable ONNX 6-DoF head-pose
model** (route b, e.g. 6DRepNet) reproduce the engine's current MediaPipe head pose well enough to
replace it? We measure the candidate against **MediaPipe's own 4x4 FaceGeometry matrix on identical
frames** — not ground truth — because MediaPipe is what the shipping engine uses today.

Metric: per-axis **RMS error (deg)** after a *deterministic* per-axis sign+offset alignment (the
engine calibrates neutral/sign anyway — B2.2 — so absolute zero/handedness is not what we're
testing; tracking *agreement* is), plus **p95 inference latency (ms)** per estimator.

Host-only (mediapipe/cv2/sixdrepnet). The pure alignment/RMS math is importable without those, so it
unit-tests on WSL/CI. Run on the Windows host:
    py -3.12 spikes/r_s1_pose_parity.py --images <dir> [--model <face_landmarker.task>]
    py -3.12 spikes/r_s1_pose_parity.py --grab-camera 120 --out <dir>   # pull live frames first
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np

AXES = ("yaw", "pitch", "roll")


def align_axis(ref: np.ndarray, model: np.ndarray) -> tuple[float, float, np.ndarray]:
    """Return ``(sign, offset, aligned_model)`` minimising RMS of ``ref - (sign*model + offset)``.

    ``sign`` ∈ {+1, -1} absorbs a handedness difference; ``offset`` absorbs the neutral-pose zero.
    Both are a single scalar over the whole set (not per-frame), so this cannot mask real tracking
    disagreement — it only removes the calibration-defined degrees of freedom.
    """
    best = None
    for sign in (1.0, -1.0):
        offset = float(np.mean(ref - sign * model))
        resid = ref - (sign * model + offset)
        rms = float(np.sqrt(np.mean(resid**2)))
        if best is None or rms < best[0]:
            best = (rms, sign, offset)
    _, sign, offset = best
    return sign, offset, sign * model + offset


def parity_rms(ref: dict[str, np.ndarray], model: dict[str, np.ndarray]) -> dict[str, dict]:
    """Per-axis RMS (deg) + alignment params between two estimators' angles over a sample set."""
    out: dict[str, dict] = {}
    for ax in AXES:
        r, m = np.asarray(ref[ax], float), np.asarray(model[ax], float)
        if r.shape != m.shape or r.size == 0:
            raise ValueError(
                f"axis {ax}: ref/model shape mismatch or empty ({r.shape} vs {m.shape})")
        sign, offset, aligned = align_axis(r, m)
        resid = r - aligned
        out[ax] = {
            "rms_deg": round(float(np.sqrt(np.mean(resid**2))), 3),
            "max_abs_deg": round(float(np.max(np.abs(resid))), 3),
            "sign": sign, "offset_deg": round(offset, 3),
            "ref_range_deg": round(float(np.ptp(r)), 1),
        }
    return out


def latency_stats(samples_ms: list[float]) -> dict:
    a = np.asarray(samples_ms, float)
    return {"n": int(a.size), "mean_ms": round(float(a.mean()), 2),
            "p50_ms": round(float(np.percentile(a, 50)), 2),
            "p95_ms": round(float(np.percentile(a, 95)), 2),
            "max_ms": round(float(a.max()), 2)}


# ---------------------------------------------------------------------------
# Host-only below (mediapipe / cv2 / sixdrepnet imported lazily inside main).
# ---------------------------------------------------------------------------
def _grab_camera(n: int, out_dir: Path, url: str) -> int:
    """Pull ``n`` distinct JPEG frames from the running engine's /camera MJPEG stream.

    Uses the engine's own camera (no device conflict) — the frames are the owner's live face, so
    pose variety comes from the owner moving naturally while this runs. Privacy: the owner's own
    camera, written only to a local dir.
    """
    import urllib.request
    out_dir.mkdir(parents=True, exist_ok=True)
    stream = urllib.request.urlopen(url, timeout=10)
    buf = b""
    saved = 0
    last = time.monotonic()
    while saved < n:
        buf += stream.read(4096)
        a, b = buf.find(b"\xff\xd8"), buf.find(b"\xff\xd9")
        if a != -1 and b != -1 and b > a:
            jpg, buf = buf[a:b + 2], buf[b + 2:]
            now = time.monotonic()
            if now - last >= 0.25:  # ~4 fps: spread samples so poses vary, not near-duplicates
                (out_dir / f"frame_{saved:04d}.jpg").write_bytes(jpg)
                saved += 1
                last = now
    print(f"grabbed {saved} frames -> {out_dir}")
    return saved


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="R-S1 pose-parity spike")
    ap.add_argument("--images", type=Path, help="dir of face images to compare on")
    ap.add_argument("--model", type=Path,
                    default=Path(r"C:\Users\ibarz\BinauralHeadtracker\face_landmarker.task"))
    ap.add_argument("--grab-camera", type=int, default=0, help="grab N frames from /camera first")
    ap.add_argument("--out", type=Path, default=Path("rs1_frames"))
    ap.add_argument("--camera-url", default="http://127.0.0.1:8731/camera")
    args = ap.parse_args(argv)

    if args.grab_camera:
        _grab_camera(args.grab_camera, args.out, args.camera_url)
        if not args.images:
            args.images = args.out

    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    import cv2
    import mediapipe as mp
    from mediapipe.tasks.python import vision

    from binaural_headtracker.head_pose import head_pose_from_matrix

    lm = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(args.model)),
        output_facial_transformation_matrixes=True, num_faces=1))

    from sixdrepnet import SixDRepNet
    sixd = SixDRepNet()

    imgs = sorted(p for p in args.images.iterdir()
                  if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
    if not imgs:
        print(f"no images in {args.images}", file=sys.stderr)
        return 2

    ref = {ax: [] for ax in AXES}
    mdl = {ax: [] for ax in AXES}
    mp_lat: list[float] = []
    sd_lat: list[float] = []
    used = 0
    for p in imgs:
        bgr = cv2.imread(str(p))
        if bgr is None:
            continue
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        t0 = time.perf_counter()
        res = lm.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
        t1 = time.perf_counter()
        if not res.facial_transformation_matrixes:
            continue  # no face detected by MediaPipe -> can't form a reference
        yaw_m, pitch_m, roll_m = head_pose_from_matrix(
            np.array(res.facial_transformation_matrixes[0]))
        t2 = time.perf_counter()
        try:
            pitch_s, yaw_s, roll_s = sixd.predict(bgr)
        except Exception as e:  # noqa: BLE001 — a model that can't predict on a frame is a miss
            print(f"{p.name}: 6DRepNet predict failed: {e}", file=sys.stderr)
            continue
        t3 = time.perf_counter()
        for ax, v in zip(AXES, (yaw_m, pitch_m, roll_m)):
            ref[ax].append(float(v))
        for ax, v in zip(AXES, (yaw_s, pitch_s, roll_s)):
            mdl[ax].append(float(np.ravel(v)[0]))
        mp_lat.append((t1 - t0) * 1000.0)
        sd_lat.append((t3 - t2) * 1000.0)
        used += 1

    if used < 3:
        print(f"only {used} frames had a detectable face in BOTH — need >=3 for a meaningful RMS",
              file=sys.stderr)
        return 3

    rms = parity_rms(ref, mdl)
    print(f"\n=== R-S1 pose parity: 6DRepNet vs MediaPipe ({used} frames) ===")
    for ax in AXES:
        s = rms[ax]
        print(f"  {ax:5s}: RMS {s['rms_deg']:6.2f}°  max {s['max_abs_deg']:6.2f}°  "
              f"(ref range {s['ref_range_deg']:.0f}°, sign {s['sign']:+.0f}, "
              f"offset {s['offset_deg']:+.1f}°)")
    print(f"  combined RMS: {np.sqrt(np.mean([rms[a]['rms_deg']**2 for a in AXES])):.2f}°")
    print(f"  latency MediaPipe: {latency_stats(mp_lat)}")
    print(f"  latency 6DRepNet : {latency_stats(sd_lat)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
