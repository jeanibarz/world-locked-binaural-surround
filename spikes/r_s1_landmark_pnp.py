"""R-S1 candidate (3): light face-landmark net → Procrustes/PnP, validated against MediaPipe.

The ownable recipe that mirrors MediaPipe's *own* FaceGeometry: a light landmark net (MediaPipe's
FaceMesh, Apache-2.0, ONNX-convertible) + a cheap geometric solve. This spike validates the
**accuracy half** before any ONNX conversion by solving pose from MediaPipe's *landmark output*
(`cv2.solvePnP`, canonical 3D ↔ detected 2D) and checking it reproduces MediaPipe's own
FaceGeometry 4×4 matrix. If it does, the recipe is sound near-by-construction (same landmarks), and
the only remaining work is packaging: the landmark net → ONNX + the solve → Rust. Perf is ~MediaPipe
by construction (it IS MediaPipe's net) + a negligible SVD/PnP.

Host-only (mediapipe/cv2). Run: py -3.12 spikes/r_s1_landmark_pnp.py --images <dir> --canonical <obj>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np


def load_canonical(obj_path: Path) -> np.ndarray:
    """Parse the 468 metric 3D vertices from canonical_face_model.obj → (468, 3)."""
    verts = []
    for ln in obj_path.read_text().splitlines():
        if ln.startswith("v "):
            _, x, y, z = ln.split()[:4]
            verts.append((float(x), float(y), float(z)))
    v = np.asarray(verts, dtype=np.float64)
    # MediaPipe's canonical model is OpenGL-style (y up, z toward viewer); solvePnP + image pixels
    # are OpenCV (y down, z into scene). Flip Y and Z into the camera frame so the solved rotation —
    # and its yaw/pitch/roll — match a standard head pose (otherwise pitch comes out reflected).
    v[:, 1] *= -1.0
    v[:, 2] *= -1.0
    return v


# a stable, rigid subset of FaceMesh indices (brow/eye-corners/nose/jaw hinge) — avoids the mobile
# lips/cheeks that a plain all-468 PnP would let expressions perturb.
RIGID_IDX = [33, 263, 1, 61, 291, 199, 133, 362, 168, 6, 197, 195, 5, 4, 98, 327, 234, 454, 10, 152]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", type=Path, required=True)
    ap.add_argument("--canonical", type=Path, default=Path(r"C:\Users\ibarz\canonical_face_model.obj"))
    ap.add_argument("--model", type=Path,
                    default=Path(r"C:\Users\ibarz\BinauralHeadtracker\face_landmarker.task"))
    ap.add_argument("--rigid", action="store_true", help="use the rigid landmark subset for PnP")
    args = ap.parse_args(argv)

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import importlib.util
    _s = importlib.util.spec_from_file_location("rs1par", Path(__file__).parent / "r_s1_pose_parity.py")
    rs1 = importlib.util.module_from_spec(_s); _s.loader.exec_module(rs1)

    import cv2
    import mediapipe as mp
    from mediapipe.tasks.python import vision

    from binaural_headtracker.head_pose import head_pose_from_matrix, rotation_matrix_to_euler

    canonical = load_canonical(args.canonical)  # (468,3)
    idx = RIGID_IDX if args.rigid else list(range(468))
    obj_pts = canonical[idx].astype(np.float64)

    lm = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(args.model)),
        output_facial_transformation_matrixes=True, num_faces=1))

    imgs = sorted(p for p in args.images.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
    ref = {ax: [] for ax in rs1.AXES}
    mdl = {ax: [] for ax in rs1.AXES}
    used = 0
    for p in imgs:
        bgr = cv2.imread(str(p))
        if bgr is None:
            continue
        h, w = bgr.shape[:2]
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        res = lm.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
        if not res.facial_transformation_matrixes or not res.face_landmarks:
            continue
        yaw_m, pitch_m, roll_m = head_pose_from_matrix(np.array(res.facial_transformation_matrixes[0]))
        lms = res.face_landmarks[0]
        img_pts = np.array([[lms[i].x * w, lms[i].y * h] for i in idx], dtype=np.float64)
        f = float(w)  # a reasonable pinhole focal ~ image width
        K = np.array([[f, 0, w / 2.0], [0, f, h / 2.0], [0, 0, 1]], dtype=np.float64)
        # SQPNP is global + flip-robust; ITERATIVE can converge to a ~180° roll-flipped local min
        # on near-planar face landmarks, which inflates roll RMS with spurious flips.
        flag = getattr(cv2, "SOLVEPNP_SQPNP", cv2.SOLVEPNP_EPNP)
        ok, rvec, _ = cv2.solvePnP(obj_pts, img_pts, K, None, flags=flag)
        if not ok:
            continue
        R, _ = cv2.Rodrigues(rvec)
        yaw_p, pitch_p, roll_p = rotation_matrix_to_euler(R)
        for ax, v in zip(rs1.AXES, (yaw_m, pitch_m, roll_m)):
            ref[ax].append(float(v))
        for ax, v in zip(rs1.AXES, (yaw_p, pitch_p, roll_p)):
            mdl[ax].append(float(v))
        used += 1

    if used < 3:
        print(f"only {used} usable frames", file=sys.stderr)
        return 3
    r = rs1.parity_rms(ref, mdl)
    print(f"\n=== R-S1 (3) landmark->PnP vs MediaPipe FaceGeometry ({used} frames, "
          f"{'rigid-subset' if args.rigid else 'all-468'}) ===")
    refa = {ax: np.asarray(ref[ax], float) for ax in rs1.AXES}
    opm = np.ones(used, bool)
    for ax, lim in [("yaw", 45.0), ("pitch", 30.0), ("roll", 30.0)]:
        opm &= np.abs(refa[ax]) <= lim
    for ax in rs1.AXES:
        s = r[ax]
        aligned = s["sign"] * np.asarray(mdl[ax], float) + s["offset_deg"]
        resid = rs1._wrap_deg(refa[ax] - aligned)
        p50 = float(np.median(np.abs(resid)))
        op = float(np.sqrt(np.mean(resid[opm] ** 2))) if opm.sum() >= 3 else float("nan")
        print(f"  {ax:5s}: RMS {s['rms_deg']:5.2f}°  p50 {p50:4.2f}°  op-range RMS {op:5.2f}°")
    print(f"  combined RMS: {np.sqrt(np.mean([r[a]['rms_deg']**2 for a in rs1.AXES])):.2f}°  "
          f"(op-range frames {int(opm.sum())}/{used})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
