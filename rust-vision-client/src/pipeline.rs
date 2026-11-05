//! The Rust-native head-pose pipeline: image → ONNX BlazeFace detect → FaceMesh landmarks → Kabsch
//! pose. This is the zero-Python body that replaces the MediaPipe sidecar; it owns both nets as
//! static ONNX assets and produces the same `(yaw, pitch, roll)` the engine consumed from
//! FaceGeometry.
//!
//! Only built with the `inference` feature (pulls `ort` + `image`).

use ort::session::Session;
use ort::value::Tensor;

use crate::anchors::{short_range_anchors, Anchor};
use crate::detector::detections;
use crate::image_util::RgbImage;
use crate::roi::detection_to_rect;
use crate::solve::solve_pose;

/// The 20-point rigid FaceMesh subset the Kabsch solve aligns (brow/eye-corners/nose/jaw hinge) —
/// identical to the spike's `RIGID_IDX`. Stable against expression (avoids mobile lips/cheeks).
pub const RIGID_IDX: [usize; 20] =
    [33, 263, 1, 61, 291, 199, 133, 362, 168, 6, 197, 195, 5, 4, 98, 327, 234, 454, 10, 152];

const NUM_LANDMARKS: usize = 478;

/// One head-pose estimate plus the landmarks it came from (for guard-1 parity checks).
pub struct Estimate {
    /// (yaw, pitch, roll) in degrees — the engine's `Rz·Ry·Rx` convention.
    pub pose: (f64, f64, f64),
    /// All 478 landmarks, MediaPipe's NormalizedLandmark convention: x∈[0,1] of width, y∈[0,1] of
    /// height, z normalised by image width.
    pub landmarks: Vec<[f64; 3]>,
    /// Detector confidence of the chosen face.
    pub score: f32,
}

/// The loaded pipeline: both ONNX sessions + the precomputed anchors + the canonical rigid model.
pub struct Pipeline {
    detector: Session,
    mesh: Session,
    anchors: Vec<Anchor>,
    /// Canonical rigid subset (20×3), OpenCV frame (Y,Z flipped), in `RIGID_IDX` order.
    canonical_rigid: Vec<[f64; 3]>,
}

impl Pipeline {
    /// Load `facedet.onnx` + `facemesh.onnx` from `onnx_dir` and the canonical model from
    /// `canonical_obj` (MediaPipe's `canonical_face_model.obj`, 468 verts).
    ///
    /// Returns `Err(String)` (not a panic) for a missing/malformed ONNX net **or** canonical OBJ, so
    /// a bad asset surfaces as a clean error the caller can log, symmetrically for both inputs
    /// (BUG-11 — previously a missing/short OBJ panicked the pose thread).
    ///
    /// ONNX intra-op threads are capped (BUG-12) so inference doesn't grab every core and starve the
    /// real-time audio render thread; the two nets run on a small pool, not all cores.
    pub fn new(onnx_dir: &str, canonical_obj: &str) -> Result<Self, String> {
        let intra = onnx_intra_threads();
        let detector = Session::builder()
            .and_then(configure_providers)
            .and_then(|b| b.with_intra_threads(intra))
            .and_then(|b| b.commit_from_file(format!("{onnx_dir}/facedet.onnx")))
            .map_err(|e| format!("load facedet.onnx: {e}"))?;
        let mesh = Session::builder()
            .and_then(configure_providers)
            .and_then(|b| b.with_intra_threads(intra))
            .and_then(|b| b.commit_from_file(format!("{onnx_dir}/facemesh.onnx")))
            .map_err(|e| format!("load facemesh.onnx: {e}"))?;
        let canonical_rigid = load_canonical_rigid(canonical_obj)?;
        Ok(Self { detector, mesh, anchors: short_range_anchors(), canonical_rigid })
    }

    /// Run the full pipeline on one image. `Ok(None)` when no face is found: either no detector box
    /// clears the threshold, or FaceMesh's face-presence score rejects the crop (issue #211).
    ///
    /// Returns `Err(String)` (never a panic) for an ONNX runtime error, a missing output tensor, or
    /// an output of the wrong length — so a mismatched model surfaces as a diagnosable error.
    pub fn estimate(&mut self, img: &RgbImage) -> Result<Option<Estimate>, String> {
        let (det_tensor, lb) = crate::image_util::letterbox_128(img);
        let det_in = Tensor::from_array(([1_i64, 128, 128, 3], det_tensor)).map_err(ort_err)?;
        let det_out = self.detector.run(ort::inputs! { "input" => det_in }).map_err(ort_err)?;
        let regressors = named_f32(&det_out, "regressors")?;
        let scores = named_f32(&det_out, "classificators")?;
        let dets = detections(regressors, scores, &self.anchors)?;
        let Some(best) = dets.into_iter().next() else {
            return Ok(None); // weighted_nms keeps the global max first
        };
        let score = best.score;

        let rect = detection_to_rect(&best, &lb);
        let crop = rect.crop_256(img);
        let mesh_in = Tensor::from_array(([1_i64, 256, 256, 3], crop)).map_err(ort_err)?;
        let mesh_out = self.mesh.run(ort::inputs! { "input_12" => mesh_in }).map_err(ort_err)?;

        // Gate on FaceMesh's face-presence output before trusting the landmarks (issue #211).
        // BlazeFace false-positives on posters and partly-occluded or turned heads; feeding such a
        // crop to FaceMesh still yields 478 landmarks, so the old code always returned
        // `Some(Estimate)` and the garbage landmarks solved to a random yaw that jumped the
        // world-lock onto a non-face. FaceMesh emits a separate face-presence score; `Identity_1`
        // is that score. The output names were verified for the bundled facemesh.onnx via the
        // source face_landmarks_detector.tflite metadata (order preserved by tf2onnx) and live
        // inference: Identity=face_landmarks (1434), Identity_1=presence (a RAW logit),
        // Identity_2=tongue_out (already sigmoided in-graph — gating on it could never reject).
        // So sigmoid-activate `Identity_1` and drop detections below the threshold. Read before the
        // landmark projection/Kabsch so a rejected frame costs nothing extra.
        let presence = named_f32(&mesh_out, "Identity_1")?; // 1×1×1×1 presence logit
        let presence_logit = *presence.first().ok_or("facemesh presence tensor is empty")?;
        if !face_present(presence_logit) {
            return Ok(None);
        }

        let lm = named_f32(&mesh_out, "Identity")?; // 1×1×1×1434
        check_landmark_len(lm.len())?;

        // Project all 478 landmarks back to the image (NormalizedLandmark convention).
        let (w, h) = (lb.img_w as f64, lb.img_h as f64);
        let mut landmarks = Vec::with_capacity(NUM_LANDMARKS);
        for i in 0..NUM_LANDMARKS {
            let p = rect.project_landmark(lm[i * 3], lm[i * 3 + 1], lm[i * 3 + 2]); // [img_x, img_y, z*side]
            landmarks.push([p[0] / w, p[1] / h, p[2] / w]);
        }

        // Kabsch on the rigid subset in the solve's pixel convention: [x*w, y*h, z*w].
        let detected: Vec<[f64; 3]> = RIGID_IDX
            .iter()
            .map(|&i| {
                let l = landmarks[i];
                [l[0] * w, l[1] * h, l[2] * w]
            })
            .collect();
        let pose = solve_pose(&self.canonical_rigid, &detected);
        Ok(Some(Estimate { pose, landmarks, score }))
    }
}

fn ort_err(e: ort::Error) -> String {
    e.to_string()
}

/// Look up the named f32 output tensor's data, returning a descriptive `Err` (not a panic) if the
/// model has no such output or it isn't an f32 tensor.
pub(crate) fn named_f32<'a>(
    outputs: &'a ort::session::SessionOutputs<'_>,
    name: &str,
) -> Result<&'a [f32], String> {
    let v = outputs.get(name).ok_or_else(|| format!("model output {name:?} not found"))?;
    let (_, data) = v
        .try_extract_tensor::<f32>()
        .map_err(|e| format!("model output {name:?}: {e}"))?;
    Ok(data)
}

/// The FaceMesh landmark tensor must hold exactly `NUM_LANDMARKS`×3 floats; a different model (e.g.
/// the 468-point mesh, 1404 floats) would otherwise index out of bounds.
fn check_landmark_len(len: usize) -> Result<(), String> {
    let want = NUM_LANDMARKS * 3;
    if len == want {
        Ok(())
    } else {
        Err(format!("facemesh landmark tensor length {len} != {want} ({NUM_LANDMARKS}x3)"))
    }
}

/// Minimum face-presence probability for a detection to be trusted. FaceMesh's `Identity_1`
/// ("presence") output is a raw logit; a crop whose `sigmoid(logit)` is below this is treated as a
/// BlazeFace false positive and dropped (`estimate` returns `Ok(None)`) instead of published as a
/// garbage-yaw pose (issue #211). 0.5 is the natural logit-sign boundary (`sigmoid(0) == 0.5`).
const FACE_PRESENCE_THRESHOLD: f32 = 0.5;

/// Logistic sigmoid — maps FaceMesh's raw presence logit to a probability in `[0, 1]`.
fn sigmoid(x: f32) -> f32 {
    1.0 / (1.0 + (-x).exp())
}

/// Whether a FaceMesh presence logit clears [`FACE_PRESENCE_THRESHOLD`] once sigmoid-activated.
fn face_present(presence_logit: f32) -> bool {
    sigmoid(presence_logit) >= FACE_PRESENCE_THRESHOLD
}

/// Number of ONNX intra-op threads: a small cap so the two nets don't grab every core and starve
/// the real-time audio render thread (BUG-12). ~1/4 of the machine's cores, clamped to 1..=2.
pub(crate) fn onnx_intra_threads() -> usize {
    (std::thread::available_parallelism().map(|n| n.get()).unwrap_or(4) / 4).clamp(1, 2)
}

/// Attach the GPU execution provider to a session builder when compiled with `gpu-directml`.
///
/// Face detection + mesh are the dominant head-tracking latency: on the CPU default provider (with
/// the capped 1-2 intra-op threads above) each solve is ~100 ms, so the pose rate is inference-bound
/// at ~9 Hz regardless of camera fps. DirectML offloads both nets to a DirectX 12 GPU, cutting that
/// to ~10-20 ms and freeing the CPU for the real-time audio thread.
///
/// Registration is best-effort: `ort`'s `error_on_failure` defaults off, so on a machine without a
/// usable DX12 GPU it logs and keeps the CPU path — never failing session creation. DirectML needs
/// the ORT memory planner disabled, which we do here. Without the feature this is a no-op (the
/// `DirectMLExecutionProvider` type isn't even linked), so non-Windows / CPU builds are unchanged.
pub(crate) fn configure_providers(builder: ort::session::builder::SessionBuilder) -> ort::Result<ort::session::builder::SessionBuilder> {
    #[cfg(feature = "gpu-directml")]
    {
        use ort::execution_providers::directml::DirectMLExecutionProvider;
        return builder
            .with_execution_providers([DirectMLExecutionProvider::default().build()])?
            .with_memory_pattern(false);
    }
    #[cfg(not(feature = "gpu-directml"))]
    {
        Ok(builder)
    }
}

/// Parse `canonical_face_model.obj`, flip Y/Z (OpenGL→OpenCV), and take the `RIGID_IDX` subset.
/// Returns `Err` (not a panic) on a missing/unreadable file, a malformed `v` line, or a model with
/// too few vertices to cover `RIGID_IDX` — so a bad asset can't crash the pose thread (BUG-11).
pub fn load_canonical_rigid(obj_path: &str) -> Result<Vec<[f64; 3]>, String> {
    let text = std::fs::read_to_string(obj_path)
        .map_err(|e| format!("read canonical obj {obj_path}: {e}"))?;
    let mut verts: Vec<[f64; 3]> = Vec::new();
    for (ln, line) in text.lines().enumerate() {
        if let Some(rest) = line.strip_prefix("v ") {
            let mut it = rest.split_whitespace();
            let mut next = |axis: &str| -> Result<f64, String> {
                it.next()
                    .ok_or_else(|| format!("canonical obj line {}: missing {axis}", ln + 1))?
                    .parse::<f64>()
                    .map_err(|e| format!("canonical obj line {}: bad {axis}: {e}", ln + 1))
            };
            let x = next("x")?;
            let y = next("y")?;
            let z = next("z")?;
            verts.push([x, -y, -z]); // OpenGL -> OpenCV frame
        }
    }
    let need = RIGID_IDX.iter().copied().max().unwrap_or(0);
    if verts.len() <= need {
        return Err(format!(
            "canonical obj has {} vertices; need > {need} for the rigid subset",
            verts.len()
        ));
    }
    Ok(RIGID_IDX.iter().map(|&i| verts[i]).collect())
}

/// Decode a JPEG/PNG byte buffer into an [`RgbImage`] (f32 [0,255], R,G,B).
pub fn decode_image(bytes: &[u8]) -> Result<RgbImage, image::ImageError> {
    let dyn_img = image::load_from_memory(bytes)?;
    let rgb = dyn_img.to_rgb8();
    let (w, h) = (rgb.width() as usize, rgb.height() as usize);
    let px: Vec<f32> = rgb.into_raw().into_iter().map(|b| b as f32).collect();
    Ok(RgbImage { w, h, px })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn landmark_len_validated() {
        assert!(check_landmark_len(1434).is_ok());
        assert!(check_landmark_len(1404).is_err()); // 468-point mesh
        assert!(check_landmark_len(0).is_err());
    }

    #[test]
    fn sigmoid_centered_and_saturates() {
        assert!((sigmoid(0.0) - 0.5).abs() < 1e-6);
        assert!(sigmoid(20.0) > 0.99); // large positive logit -> ~1
        assert!(sigmoid(-20.0) < 0.01); // large negative logit -> ~0
    }

    #[test]
    fn presence_gate_rejects_false_positives() {
        // A real FaceMesh crop yields a large positive presence logit (measured on the bundled
        // facemesh.onnx: a face crop scores tens-to-hundreds) -> accepted.
        assert!(face_present(57.5));
        assert!(face_present(10.0));
        // Logit 0 is the boundary; sigmoid(0) == 0.5 >= threshold -> accepted.
        assert!(face_present(0.0));
        // A BlazeFace false positive (poster / occluded / turned head) scores a negative presence
        // logit (measured: a non-face crop ~-14) -> rejected, so no garbage yaw is published.
        assert!(!face_present(-0.1));
        assert!(!face_present(-14.0));
    }
}
