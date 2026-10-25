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
    pub fn new(onnx_dir: &str, canonical_obj: &str) -> ort::Result<Self> {
        let detector = Session::builder()?.commit_from_file(format!("{onnx_dir}/facedet.onnx"))?;
        let mesh = Session::builder()?.commit_from_file(format!("{onnx_dir}/facemesh.onnx"))?;
        let canonical_rigid = load_canonical_rigid(canonical_obj);
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

/// Parse `canonical_face_model.obj`, flip Y/Z (OpenGL→OpenCV), and take the `RIGID_IDX` subset.
pub fn load_canonical_rigid(obj_path: &str) -> Vec<[f64; 3]> {
    let text = std::fs::read_to_string(obj_path).expect("read canonical obj");
    let mut verts: Vec<[f64; 3]> = Vec::new();
    for line in text.lines() {
        if let Some(rest) = line.strip_prefix("v ") {
            let mut it = rest.split_whitespace();
            let x: f64 = it.next().unwrap().parse().unwrap();
            let y: f64 = it.next().unwrap().parse().unwrap();
            let z: f64 = it.next().unwrap().parse().unwrap();
            verts.push([x, -y, -z]); // OpenGL -> OpenCV frame
        }
    }
    RIGID_IDX.iter().map(|&i| verts[i]).collect()
}

/// Decode a JPEG/PNG byte buffer into an [`RgbImage`] (f32 [0,255], R,G,B).
pub fn decode_image(bytes: &[u8]) -> Result<RgbImage, image::ImageError> {
    let dyn_img = image::load_from_memory(bytes)?;
    let rgb = dyn_img.to_rgb8();
    let (w, h) = (rgb.width() as usize, rgb.height() as usize);
    let px: Vec<f32> = rgb.into_raw().into_iter().map(|b| b as f32).collect();
    Ok(RgbImage { w, h, px })
}
