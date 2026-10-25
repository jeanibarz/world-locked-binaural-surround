//! Head-pose solve: rigid 3D alignment (Umeyama/Kabsch) of the canonical face model to the
//! detected landmarks → (yaw, pitch, roll). The ownable, Rust-native replacement for MediaPipe's
//! FaceGeometry solve (R-S1 candidate 3). Validated in the Python spike: on AFLW2000 it reproduces
//! MediaPipe's own pose to ~3.9°/1.4°/0.8° (yaw/pitch/roll) RMS in the ±45/30/30 operating range.
//!
//! This module ports the *solve* bit-for-bit from the spike (same Umeyama + the engine's
//! `rotation_matrix_to_euler` decomposition), so the parity test matches the Python reference to
//! numerical precision. Landmark-net inference (ONNX via `ort`) and the detector/ROI feed it.

use nalgebra::{Matrix3, Vector3};

/// Rigid rotation aligning `src` onto `dst` (both N×3, same order) by Umeyama/Kabsch — the rotation
/// `R` minimising `‖R·(src-μ_src) - (dst-μ_dst)‖`, with the reflection guard (det = ±1) so `R` is a
/// proper rotation. Mirrors numpy: `H = src_c^T·dst_c`, `U,S,Vᵀ = svd(H)`, `R = V·diag(1,1,d)·Uᵀ`,
/// `d = sign(det(V·Uᵀ))`.
pub fn umeyama_rotation(src: &[[f64; 3]], dst: &[[f64; 3]]) -> Matrix3<f64> {
    assert!(src.len() == dst.len() && !src.is_empty(), "src/dst must be same non-zero length");
    let n = src.len() as f64;
    let mean = |pts: &[[f64; 3]]| {
        let mut m = Vector3::zeros();
        for p in pts {
            m += Vector3::new(p[0], p[1], p[2]);
        }
        m / n
    };
    let (ms, md) = (mean(src), mean(dst));
    // H = Σ (src_i - ms)(dst_i - md)^T   (3×3)
    let mut h = Matrix3::zeros();
    for (s, d) in src.iter().zip(dst.iter()) {
        let sc = Vector3::new(s[0], s[1], s[2]) - ms;
        let dc = Vector3::new(d[0], d[1], d[2]) - md;
        h += sc * dc.transpose();
    }
    let svd = h.svd(true, true);
    let u = svd.u.expect("svd u");
    let vt = svd.v_t.expect("svd v_t");
    let v = vt.transpose();
    let d = (v * u.transpose()).determinant().signum();
    let dmat = Matrix3::from_diagonal(&Vector3::new(1.0, 1.0, d));
    v * dmat * u.transpose()
}

/// `(yaw, pitch, roll)` degrees from a 3×3 rotation, for `R = Rz(roll)·Ry(yaw)·Rx(pitch)` — the exact
/// decomposition the engine uses (`binaural_headtracker.head_pose.rotation_matrix_to_euler`), with
/// the same gimbal-lock guard, so pose values match the Python pipeline.
pub fn rotation_to_euler(r: &Matrix3<f64>) -> (f64, f64, f64) {
    // nalgebra is column-major; r[(row, col)] indexes like numpy r[row, col].
    let cy = (r[(0, 0)] * r[(0, 0)] + r[(1, 0)] * r[(1, 0)]).sqrt();
    let (yaw, pitch, roll) = if cy > 1e-6 {
        (
            (-r[(2, 0)]).atan2(cy),
            r[(2, 1)].atan2(r[(2, 2)]),
            r[(1, 0)].atan2(r[(0, 0)]),
        )
    } else {
        ((-r[(2, 0)]).atan2(cy), (-r[(1, 2)]).atan2(r[(1, 1)]), 0.0)
    };
    (yaw.to_degrees(), pitch.to_degrees(), roll.to_degrees())
}

/// Full solve: align `canonical` (metric 3D, OpenCV frame) to `detected` (landmark xyz) → head pose.
pub fn solve_pose(canonical: &[[f64; 3]], detected: &[[f64; 3]]) -> (f64, f64, f64) {
    rotation_to_euler(&umeyama_rotation(canonical, detected))
}
