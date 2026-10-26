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
            .and_then(|b| b.with_intra_threads(intra))
            .and_then(|b| b.commit_from_file(format!("{onnx_dir}/facedet.onnx")))
            .map_err(|e| format!("load facedet.onnx: {e}"))?;
        let mesh = Session::builder()
            .and_then(|b| b.with_intra_threads(intra))
            .and_then(|b| b.commit_from_file(format!("{onnx_dir}/facemesh.onnx")))
            .map_err(|e| format!("load facemesh.onnx: {e}"))?;
        let canonical_rigid = load_canonical_rigid(canonical_obj)?;
        Ok(Self { detector, mesh, anchors: short_range_anchors(), canonical_rigid })
    }

    /// Run the full pipeline on one image. `None` if no face clears the detector threshold.
    pub fn estimate(&mut self, img: &RgbImage) -> ort::Result<Option<Estimate>> {
        let (det_tensor, lb) = crate::image_util::letterbox_128(img);
        let det_out = self
            .detector
            .run(ort::inputs! { "input" => Tensor::from_array(([1_i64, 128, 128, 3], det_tensor))? })?;
        let (_, regressors) = det_out["regressors"].try_extract_tensor::<f32>()?;
        let (_, scores) = det_out["classificators"].try_extract_tensor::<f32>()?;
        let dets = detections(regressors, scores, &self.anchors);
        let Some(best) = dets.into_iter().next() else {
            return Ok(None); // weighted_nms keeps the global max first
        };
        let score = best.score;

        let rect = detection_to_rect(&best, &lb);
        let crop = rect.crop_256(img);
        let mesh_out = self
            .mesh
            .run(ort::inputs! { "input_12" => Tensor::from_array(([1_i64, 256, 256, 3], crop))? })?;
        let (_, lm) = mesh_out["Identity"].try_extract_tensor::<f32>()?; // 1×1×1×1434
        debug_assert_eq!(lm.len(), NUM_LANDMARKS * 3);

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

/// Number of ONNX intra-op threads: a small cap so the two nets don't grab every core and starve
/// the real-time audio render thread (BUG-12). ~1/4 of the machine's cores, clamped to 1..=2.
fn onnx_intra_threads() -> usize {
    (std::thread::available_parallelism().map(|n| n.get()).unwrap_or(4) / 4).clamp(1, 2)
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
