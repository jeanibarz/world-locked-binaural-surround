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

/// Full-circle head **azimuth** (degrees, wrapped to `(-180, 180]`) from a 3×3 rotation — the
/// azimuth of the head's forward axis (canonical `+Z`) projected onto the horizontal `x–z` plane:
/// `atan2((R·ẑ)_x, (R·ẑ)_z)`.
///
/// **Why this exists (RFC "full-range head tracking", slice C0).** [`rotation_to_euler`]'s yaw is
/// the **middle** axis of the `Rz·Ry·Rx` decomposition, so it is mathematically confined to
/// `[−90°, +90°]` (`cy = |cos(yaw)| ≥ 0`): a real 150° head turn folds back to 30°, and the gimbal
/// singularity sits at ±90° — exactly where the extended range begins. This azimuth has **no such
/// cap**: it is continuous through ±90° and spans the whole circle, so it can represent a head
/// turned past profile and toward the rear.
///
/// **Faithful to the current solver where the current solver is valid.** For a pure yaw rotation it
/// equals `rotation_to_euler`'s yaw exactly within ±90°; in fact the equality holds exactly for any
/// **yaw + pitch** (roll = 0), because the common `cos(pitch)` factor cancels inside the `atan2`.
/// The only divergence is a weak `roll × pitch` coupling (second order; roll is small in normal
/// head motion), quantified in the module tests. So this is a safe drop-in for the front cone and
/// the full-range generalization past it. Wiring the engine's scalar `yaw` / `published_yaw` /
/// azimuth onto it (wrap-safe) is the rest of slice C0; the exact handedness is pinned there against
/// the live camera.
pub fn rotation_to_azimuth(r: &Matrix3<f64>) -> f64 {
    // R · ẑ (ẑ = canonical forward) is the third column of R; its horizontal azimuth never saturates.
    let fx = r[(0, 2)];
    let fz = r[(2, 2)];
    fx.atan2(fz).to_degrees()
}

/// Full solve: align `canonical` (metric 3D, OpenCV frame) to `detected` (landmark xyz) → head pose.
pub fn solve_pose(canonical: &[[f64; 3]], detected: &[[f64; 3]]) -> (f64, f64, f64) {
    rotation_to_euler(&umeyama_rotation(canonical, detected))
}

/// Representation spike (RFC "full-range head tracking", Phase-0 0c). These tests make the RFC's
/// load-bearing claim **executable**: the current Euler decomposition caps yaw at ±90°, and the
/// forward-axis azimuth is the continuous full-circle replacement that still matches the current
/// solver in the operating range. No camera/model needed — pure rotation algebra.
#[cfg(test)]
mod full_circle_spike {
    use super::*;

    fn rx(a: f64) -> Matrix3<f64> {
        let (s, c) = a.sin_cos();
        Matrix3::new(1., 0., 0., 0., c, -s, 0., s, c)
    }
    fn ry(a: f64) -> Matrix3<f64> {
        let (s, c) = a.sin_cos();
        Matrix3::new(c, 0., s, 0., 1., 0., -s, 0., c)
    }
    fn rz(a: f64) -> Matrix3<f64> {
        let (s, c) = a.sin_cos();
        Matrix3::new(c, -s, 0., s, c, 0., 0., 0., 1.)
    }
    /// The engine's convention: `R = Rz(roll)·Ry(yaw)·Rx(pitch)` (see `rotation_to_euler` doc).
    fn compose(yaw_deg: f64, pitch_deg: f64, roll_deg: f64) -> Matrix3<f64> {
        rz(roll_deg.to_radians()) * ry(yaw_deg.to_radians()) * rx(pitch_deg.to_radians())
    }
    /// Signed smallest difference a−b on the circle, in degrees (−180, 180].
    fn circ_diff(a: f64, b: f64) -> f64 {
        (((a - b) + 180.0).rem_euclid(360.0)) - 180.0
    }

    #[test]
    fn euler_yaw_caps_at_90_degrees() {
        // THE problem, executable: a pure yaw past 90° folds back under rotation_to_euler.
        for (true_yaw, folded) in
            [(60.0, 60.0), (90.0, 90.0), (120.0, 60.0), (150.0, 30.0), (170.0, 10.0)]
        {
            let (y, _, _) = rotation_to_euler(&compose(true_yaw, 0.0, 0.0));
            assert!(
                (y - folded).abs() < 1e-6,
                "pure yaw {true_yaw}° decodes to euler yaw {y}° (folds to {folded}°) — the ±90° cap"
            );
        }
    }

    #[test]
    fn azimuth_is_full_circle_for_pure_yaw() {
        // THE fix: the forward-axis azimuth tracks the true yaw around the whole circle.
        for true_yaw in [-170.0, -120.0, -90.0, -45.0, 0.0, 30.0, 60.0, 90.0, 120.0, 170.0, 180.0] {
            let az = rotation_to_azimuth(&compose(true_yaw, 0.0, 0.0));
            assert!(
                circ_diff(az, true_yaw).abs() < 1e-6,
                "pure yaw {true_yaw}° -> azimuth {az}° (should equal it, full-circle)"
            );
        }
    }

    #[test]
    fn azimuth_equals_euler_yaw_when_roll_is_zero() {
        // Safe drop-in: identical to the current solver wherever the current solver is valid — and
        // the equality holds for ANY pitch (the cos(pitch) factor cancels in the atan2), not just 0.
        let mut yaw = -85.0;
        while yaw <= 85.0 {
            for pitch in [-30.0, -15.0, 0.0, 15.0, 30.0] {
                let r = compose(yaw, pitch, 0.0);
                let (ey, _, _) = rotation_to_euler(&r);
                let az = rotation_to_azimuth(&r);
                assert!(
                    (az - ey).abs() < 1e-9,
                    "yaw {yaw}, pitch {pitch}: azimuth {az} != euler yaw {ey}"
                );
            }
            yaw += 5.0;
        }
    }

    #[test]
    fn azimuth_is_monotonic_through_the_rear() {
        // A continuous 0°→180° sweep stays strictly increasing (no fold) — the property the world-
        // lock needs and that rotation_to_euler cannot provide past 90°.
        let mut prev_unwrapped = f64::NEG_INFINITY;
        let mut unwrapped = 0.0;
        let mut last = rotation_to_azimuth(&compose(0.0, 0.0, 0.0));
        for step in 1..=36 {
            let yaw = step as f64 * 5.0;
            let az = rotation_to_azimuth(&compose(yaw, 0.0, 0.0));
            unwrapped += circ_diff(az, last);
            last = az;
            assert!(
                unwrapped > prev_unwrapped,
                "azimuth must keep increasing; at yaw {yaw}° unwrapped={unwrapped}"
            );
            prev_unwrapped = unwrapped;
        }
        assert!((unwrapped - 180.0).abs() < 1e-6, "0→180° sweep should accumulate 180°, got {unwrapped}");
    }

    #[test]
    fn azimuth_roll_pitch_coupling_is_bounded_and_the_only_divergence() {
        // Honest limit for C0: with BOTH roll and pitch present the azimuth diverges from the Euler
        // yaw by a small, bounded amount (second-order in roll×pitch). At an extreme corner of the
        // operating box (roll 20°, pitch 30°) it stays within ~10°, and it is zero whenever roll=0.
        let r = compose(40.0, 30.0, 20.0);
        let (ey, _, _) = rotation_to_euler(&r);
        let az = rotation_to_azimuth(&r);
        let d = (az - ey).abs();
        assert!(d > 1e-3, "expected a (small) divergence when roll and pitch are both nonzero");
        assert!(d < 10.0, "roll×pitch coupling should stay bounded (<10° here), got {d}°");
    }
}
