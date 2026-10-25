"""R-S1 FORMAL gate: candidate (3) vs MediaPipe, both against AFLW2000-3D ground truth.

The operator's bar: in the ±45° yaw / ±30° pitch-roll operating range, candidate (3)'s per-axis
GT-RMS must be no worse than the incumbent MediaPipe's by more than ~1.5–2°/axis (i.e. as accurate
as what ships). Extreme-profile (>45° yaw) is out of scope — reported, not gated.

GT = AFLW2000-3D's `Pose_Para` (the first 3 values are pitch, yaw, roll in radians). Each estimator
is aligned to GT by a per-axis sign+offset (convention/calibration is not what we test — error is),
then GT-RMS is computed in-range. Host-only (mediapipe/cv2/scipy).
Run: py -3.12 spikes/r_s1_gt_gate.py --images <dir of image*.jpg + image*.mat>
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import numpy as np


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", type=Path, required=True)
    ap.add_argument("--canonical", type=Path, default=Path(r"C:\Users\ibarz\canonical_face_model.obj"))
    ap.add_argument("--model", type=Path,
                    default=Path(r"C:\Users\ibarz\BinauralHeadtracker\face_landmarker.task"))
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(here.parent / "src"))
    _s = importlib.util.spec_from_file_location("rs1", here / "r_s1_pose_parity.py")
    rs1 = importlib.util.module_from_spec(_s); _s.loader.exec_module(rs1)

    import cv2
    import mediapipe as mp
    from mediapipe.tasks.python import vision
    from scipy.io import loadmat

    from binaural_headtracker.head_pose import head_pose_from_matrix, rotation_matrix_to_euler

    V = []
    for ln in args.canonical.read_text().splitlines():
        if ln.startswith("v "):
            x, y, z = ln.split()[1:4]
            V.append((float(x), -float(y), -float(z)))  # OpenGL->OpenCV frame
    can = np.asarray(V)
    RIGID = [33, 263, 1, 61, 291, 199, 133, 362, 168, 6, 197, 195, 5, 4, 98, 327, 234, 454, 10, 152]
    canr = can[RIGID]

    def umeyama_R(src, dst):
        sc = src - src.mean(0); dc = dst - dst.mean(0)
        h = sc.T @ dc; u, _, vt = np.linalg.svd(h)
        d = np.sign(np.linalg.det(vt.T @ u.T))
        return vt.T @ np.diag([1, 1, d]) @ u.T

    lm = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(args.model)),
        output_facial_transformation_matrixes=True, num_faces=1))

    gt = {a: [] for a in rs1.AXES}
    mpp = {a: [] for a in rs1.AXES}
    kab = {a: [] for a in rs1.AXES}
    used = 0
    imgs = sorted(p for p in args.images.rglob("*.jpg"))
    for p in imgs:
        matp = p.with_suffix(".mat")
        if not matp.is_file():
            continue
        pose = np.asarray(loadmat(str(matp)).get("Pose_Para")).ravel()
        if pose.size < 3:
            continue
        g_pitch, g_yaw, g_roll = np.degrees(pose[0]), np.degrees(pose[1]), np.degrees(pose[2])
        bgr = cv2.imread(str(p))
        if bgr is None:
            continue
        h, w = bgr.shape[:2]
        res = lm.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)))
        if not res.facial_transformation_matrixes or not res.face_landmarks:
            continue
        y_mp, p_mp, r_mp = head_pose_from_matrix(np.array(res.facial_transformation_matrixes[0]))
        L = res.face_landmarks[0]
        det = np.array([[L[i].x * w, L[i].y * h, L[i].z * w] for i in RIGID])
        y_k, p_k, r_k = rotation_matrix_to_euler(umeyama_R(canr, det))
        for d, (vy, vp, vr) in ((gt, (g_yaw, g_pitch, g_roll)), (mpp, (y_mp, p_mp, r_mp)),
                                (kab, (y_k, p_k, r_k))):
            d["yaw"].append(float(vy)); d["pitch"].append(float(vp)); d["roll"].append(float(vr))
        used += 1
        if args.limit and used >= args.limit:
            break

    if used < 10:
        print(f"only {used} usable (img+mat+detect) frames", file=sys.stderr)
        return 3
    gta = {a: np.asarray(gt[a]) for a in rs1.AXES}
    opm = (np.abs(gta["yaw"]) <= 45) & (np.abs(gta["pitch"]) <= 30) & (np.abs(gta["roll"]) <= 30)

    def gt_rms(est):
        res = rs1.parity_rms(gt, est)  # aligns est to GT (sign+offset), wrapped residual
        out = {}
        for ax in rs1.AXES:
            s = res[ax]
            resid = rs1._wrap_deg(gta[ax] - (s["sign"] * np.asarray(est[ax]) + s["offset_deg"]))
            out[ax] = float(np.sqrt(np.mean(resid[opm] ** 2)))
        return out

    mp_e, kb_e = gt_rms(mpp), gt_rms(kab)
    print(f"\n=== R-S1 FORMAL GT gate ({used} frames, op-range {int(opm.sum())}; AFLW2000-3D GT) ===")
    print(f"  {'axis':6s} {'MediaPipe':>10s} {'cand(3)':>10s} {'d(c3-MP)':>10s}  bar +/-2deg")
    worst = -1e9
    for ax in rs1.AXES:
        delta = kb_e[ax] - mp_e[ax]
        worst = max(worst, delta)
        print(f"  {ax:6s} {mp_e[ax]:9.2f}° {kb_e[ax]:9.2f}° {delta:+9.2f}°")
    verdict = "PASS" if worst <= 2.0 else "FAIL"
    print(f"  worst delta = {worst:+.2f}deg  -> GT-parity {verdict} (candidate 3 no worse than "
          f"MediaPipe + 2°/axis in-range)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
