//! The full-range head-pose backend: BlazeFace detect → **6DRepNet360** (ResNet-50, 6-D rotation)
//! → a full-circle head **azimuth**. The landmark-free alternative to the FaceMesh+Kabsch path
//! (`pipeline::Pipeline`), for the RFC "full-range head tracking" estimator slice.
//!
//! Where FaceMesh loses the face past ~±50° (journal 05), 6DRepNet360 (300W-LP + CMU Panoptic)
//! regresses a full `SO(3)` rotation that stays valid through profile and into the rear. Its own
//! Euler extraction caps yaw at ±90° (the middle-axis `atan2(-R20,·)`); the full range is read off
//! the **rotation matrix** via the forward-axis azimuth `atan2(R02, R22)` (`solve::rotation_to_azimuth`,
//! journal 89), validated on a real head sweep to track continuously past ±90° (journal 91).
//!
//! Detection today still uses the frontal-ish BlazeFace short-range detector, so the usable range is
//! currently detector-bound; a head/profile-robust detector is the next step. Only built with the
//! `inference` feature (ONNX via `ort`).

use ort::session::Session;
use ort::value::Tensor;

use crate::anchors::{short_range_anchors, Anchor};
use crate::detector::detections;
use crate::image_util::{letterbox_128, RgbImage};
use crate::pipeline::{configure_providers, named_f32, onnx_intra_threads};
use crate::solve::rotation_to_azimuth;

/// ImageNet normalisation (6DRepNet preprocessing: `ToTensor` then `Normalize`), RGB order.
const MEAN: [f32; 3] = [0.485, 0.456, 0.406];
const STD: [f32; 3] = [0.229, 0.224, 0.225];
/// Net input side (6DRepNet360: 256 resize → 224 centre-crop → the net sees 224²).
const NET: usize = 224;
/// Default crop scale: detector box side × this = the square sampled around the box centre. 6DRepNet
/// wants a loose, axis-aligned face crop (it estimates roll itself, so no eye-rotation). Tunable live
/// via `WLBS_POSE360_CROP` without a rebuild — the exact value is detector-dependent and is pinned
/// against the live camera.
const DEFAULT_CROP_SCALE: f32 = 1.4;

/// One full-range head-pose estimate.
pub struct Estimate360 {
    /// Full-circle head azimuth (deg, wrapped (-180,180]) — the forward-axis azimuth of the predicted
    /// rotation; the engine consumes this as the raw head yaw.
    pub azimuth: f64,
    /// Pitch / roll (deg) for telemetry (the world-lock is yaw/azimuth-driven).
    pub pitch: f64,
    pub roll: f64,
    /// Detector confidence of the chosen face.
    pub score: f32,
}

/// The loaded full-range pipeline: BlazeFace detector + the 6DRepNet360 ResNet-50 pose net.
pub struct Pose360Pipeline {
    detector: Session,
    pose: Session,
    anchors: Vec<Anchor>,
    crop_scale: f32,
}

impl Pose360Pipeline {
    /// Load `facedet.onnx` (BlazeFace) and `sixdrepnet360.onnx` from `onnx_dir`. Capped intra-op
    /// threads + the best-effort DirectML provider, same as [`crate::pipeline::Pipeline`]. Returns
    /// `Err(String)` (never a panic) on a missing/bad asset.
    pub fn new(onnx_dir: &str) -> Result<Self, String> {
        let intra = onnx_intra_threads();
        let detector = Session::builder()
            .and_then(configure_providers)
            .and_then(|b| b.with_intra_threads(intra))
            .and_then(|b| b.commit_from_file(format!("{onnx_dir}/facedet.onnx")))
            .map_err(|e| format!("load facedet.onnx: {e}"))?;
        let pose = Session::builder()
            .and_then(configure_providers)
            .and_then(|b| b.with_intra_threads(intra))
            .and_then(|b| b.commit_from_file(format!("{onnx_dir}/sixdrepnet360.onnx")))
            .map_err(|e| format!("load sixdrepnet360.onnx: {e}"))?;
        let crop_scale = std::env::var("WLBS_POSE360_CROP")
            .ok()
            .and_then(|s| s.parse::<f32>().ok())
            .filter(|v| *v > 0.5 && *v < 4.0)
            .unwrap_or(DEFAULT_CROP_SCALE);
        Ok(Self { detector, pose, anchors: short_range_anchors(), crop_scale })
    }

    /// Run the full-range pipeline on one image. `Ok(None)` when the detector finds no face.
    /// `Err(String)` (never a panic) on an ONNX runtime error or a wrong-shaped output.
    pub fn estimate(&mut self, img: &RgbImage) -> Result<Option<Estimate360>, String> {
        let (det_tensor, lb) = letterbox_128(img);
        let det_in = Tensor::from_array(([1_i64, 128, 128, 3], det_tensor)).map_err(ort_err)?;
        let det_out = self.detector.run(ort::inputs! { "input" => det_in }).map_err(ort_err)?;
        let regressors = named_f32(&det_out, "regressors")?;
        let scores = named_f32(&det_out, "classificators")?;
        let dets = detections(regressors, scores, &self.anchors)?;
        let Some(best) = dets.into_iter().next() else {
            return Ok(None); // weighted_nms keeps the global max first
        };
        let score = best.score;

        // Axis-aligned square crop around the detection box centre (6DRepNet wants a loose, upright
        // crop — it regresses roll itself, so unlike FaceMesh's ROI we do NOT eye-rotate).
        let (x0, y0) = lb.to_image_px(best.xmin, best.ymin);
        let (x1, y1) = lb.to_image_px(best.xmax, best.ymax);
        let cx = (x0 + x1) / 2.0;
        let cy = (y0 + y1) / 2.0;
        let side = (x1 - x0).max(y1 - y0) * self.crop_scale;

        let crop = preprocess_224_nchw(img, cx, cy, side);
        let pose_in = Tensor::from_array(([1_i64, 3, NET as i64, NET as i64], crop)).map_err(ort_err)?;
        let pose_out = self.pose.run(ort::inputs! { "input" => pose_in }).map_err(ort_err)?;
        let r = named_f32(&pose_out, "R")?;
        let (azimuth, pitch, roll) = pose_from_matrix(r)?;
        Ok(Some(Estimate360 { azimuth, pitch, roll, score }))
    }
}

fn ort_err(e: ort::Error) -> String {
    e.to_string()
}

/// Sample a `side`-wide axis-aligned square centred at `(cx,cy)` into a `224×224` **NCHW** f32 tensor,
/// ImageNet-normalised (`(px/255 − mean)/std`, RGB). Border-replicate at the edges. Pure (no ONNX),
/// so native `cargo test` covers the preprocessing geometry.
pub fn preprocess_224_nchw(img: &RgbImage, cx: f32, cy: f32, side: f32) -> Vec<f32> {
    let plane = NET * NET;
    let mut t = vec![0.0f32; 3 * plane];
    for oy in 0..NET {
        for ox in 0..NET {
            let sx = cx + ((ox as f32 + 0.5) / NET as f32 - 0.5) * side;
            let sy = cy + ((oy as f32 + 0.5) / NET as f32 - 0.5) * side;
            let rgb = img.sample(sx, sy);
            for c in 0..3 {
                t[c * plane + oy * NET + ox] = (rgb[c] / 255.0 - MEAN[c]) / STD[c];
            }
        }
    }
    t
}

/// Decode 6DRepNet360's 3×3 rotation output (row-major, `R[i][j] = data[i*3+j]`) to
/// `(azimuth, pitch, roll)` degrees. Azimuth is the full-circle forward-axis angle `atan2(R02, R22)`
/// (journal 89/91); pitch/roll are the usual (range-capped, fine for telemetry) extractions.
pub fn pose_from_matrix(r: &[f32]) -> Result<(f64, f64, f64), String> {
    if r.len() != 9 {
        return Err(format!("sixdrepnet360 output length {} != 9 (3x3)", r.len()));
    }
    let m = nalgebra::Matrix3::new(
        r[0] as f64, r[1] as f64, r[2] as f64,
        r[3] as f64, r[4] as f64, r[5] as f64,
        r[6] as f64, r[7] as f64, r[8] as f64,
    );
    let azimuth = rotation_to_azimuth(&m);
    // pitch = atan2(R21, R22); roll = atan2(R10, R00) (same as the repo's compute_euler columns).
    let pitch = (m[(2, 1)]).atan2(m[(2, 2)]).to_degrees();
    let roll = (m[(1, 0)]).atan2(m[(0, 0)]).to_degrees();
    Ok((azimuth, pitch, roll))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn solid(w: usize, h: usize, rgb: [f32; 3]) -> RgbImage {
        let mut px = Vec::with_capacity(w * h * 3);
        for _ in 0..w * h {
            px.extend_from_slice(&rgb);
        }
        RgbImage { w, h, px }
    }

    #[test]
    fn preprocess_shape_and_normalisation() {
        // A solid mid-grey image: every output pixel is (128/255 − mean)/std per channel, in NCHW.
        let img = solid(300, 300, [128.0, 128.0, 128.0]);
        let t = preprocess_224_nchw(&img, 150.0, 150.0, 200.0);
        assert_eq!(t.len(), 3 * 224 * 224);
        for c in 0..3 {
            let want = (128.0f32 / 255.0 - MEAN[c]) / STD[c];
            // sample a few positions in channel plane c
            for &idx in &[0usize, 224 * 112 + 112, 224 * 223 + 223] {
                let v = t[c * 224 * 224 + idx];
                assert!((v - want).abs() < 1e-4, "ch{c} px{idx}: {v} != {want}");
            }
        }
    }

    #[test]
    fn preprocess_is_channel_first_rgb() {
        // Distinct per-channel values must land in distinct planes (NCHW), in RGB order.
        let img = solid(50, 50, [255.0, 0.0, 0.0]); // pure red
        let t = preprocess_224_nchw(&img, 25.0, 25.0, 40.0);
        let r = t[0]; // plane 0 = R
        let g = t[224 * 224]; // plane 1 = G
        let b = t[2 * 224 * 224]; // plane 2 = B
        assert!((r - (1.0 - MEAN[0]) / STD[0]).abs() < 1e-4);
        assert!((g - (0.0 - MEAN[1]) / STD[1]).abs() < 1e-4);
        assert!((b - (0.0 - MEAN[2]) / STD[2]).abs() < 1e-4);
    }

    #[test]
    fn pose_from_matrix_rejects_bad_length() {
        assert!(pose_from_matrix(&[0.0; 8]).is_err());
        assert!(pose_from_matrix(&[0.0; 9]).is_ok());
    }

    #[test]
    fn identity_rotation_is_zero_azimuth() {
        // R = I (row-major) → facing forward → azimuth 0.
        let id = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0];
        let (az, pitch, roll) = pose_from_matrix(&id).unwrap();
        assert!(az.abs() < 1e-6 && pitch.abs() < 1e-6 && roll.abs() < 1e-6);
    }

    #[test]
    fn full_circle_azimuth_tracks_past_90() {
        // A pure yaw of +150° about the vertical axis: forward axis (col 2) rotates to
        // [sin150, 0, cos150]; azimuth = atan2(sin150, cos150) = +150° — NOT folded to +30°.
        let (s, c) = 150.0f64.to_radians().sin_cos();
        // Row-major R with columns x=[c,0,-s]? Build Ry(150): [[c,0,s],[0,1,0],[-s,0,c]].
        let ry = [c as f32, 0.0, s as f32, 0.0, 1.0, 0.0, -s as f32, 0.0, c as f32];
        let (az, _, _) = pose_from_matrix(&ry).unwrap();
        assert!((az - 150.0).abs() < 1e-4, "expected +150°, got {az}");
    }
}
