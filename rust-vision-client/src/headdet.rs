//! Head detector (CrowdHuman **YOLOv11**, `Sharath33/Person`) — a profile/rear-robust detector that
//! boxes the head when BlazeFace (a *face* detector) is blind to the back of the head. Used as a
//! fallback in [`crate::headpose360::Pose360Pipeline`] so the full-circle azimuth keeps a real box
//! through the deep rear instead of coasting on a held one (journal 95). Only built with the
//! `inference` feature (ONNX via `ort`).
//!
//! The ONNX export has three decoded outputs: `boxes` (`1×8400×4`, **`cx,cy,w,h` in 640-letterbox
//! pixels**), `scores` (`1×8400×1`) and `classes` (`1×8400×1`, `0`=person, `1`=head). Input is
//! `input` = `1×3×640×640`, **BGR**, `/255`, grey (114) letterbox padding.

use ort::session::Session;
use ort::value::Tensor;

use crate::image_util::RgbImage;
use crate::pipeline::{configure_providers, named_f32, onnx_intra_threads};

/// Net input side (YOLOv11 640²).
const NET: usize = 640;
/// Letterbox pad value: grey 114, normalised by 255 like the content.
const PAD: f32 = 114.0 / 255.0;
/// Head-class id in the dual-class (0=person, 1=head) model.
const HEAD_CLASS: i32 = 1;
/// Default detection confidence threshold (matches the offline spike, journal 95).
const DEFAULT_CONF: f32 = 0.25;

/// Letterbox geometry: 640 tensor ← original image (aspect-preserving, centred, grey pad).
pub struct HeadLetterbox {
    pub scale: f32,
    pub pad_x: f32,
    pub pad_y: f32,
}

impl HeadLetterbox {
    /// Map a box centre + size from 640-tensor space to original image pixels. Returns
    /// `(cx, cy, raw_side)` with `raw_side = max(w, h)` in image px (the pose crop scale is applied by
    /// the caller, so this matches the BlazeFace path's pre-scale box).
    pub fn box_to_image(&self, cx: f32, cy: f32, w: f32, h: f32) -> (f32, f32, f32) {
        (
            (cx - self.pad_x) / self.scale,
            (cy - self.pad_y) / self.scale,
            w.max(h) / self.scale,
        )
    }
}

/// Build the 640² **NCHW, BGR, /255** input by aspect-preserving letterbox with grey (114) padding,
/// and return the mapping back to original pixels. Pure (no ONNX), so the geometry is unit-tested.
pub fn preprocess_640_nchw_bgr(img: &RgbImage) -> (Vec<f32>, HeadLetterbox) {
    let scale = NET as f32 / img.w.max(img.h) as f32;
    let scaled_w = img.w as f32 * scale;
    let scaled_h = img.h as f32 * scale;
    let pad_x = (NET as f32 - scaled_w) / 2.0;
    let pad_y = (NET as f32 - scaled_h) / 2.0;
    let plane = NET * NET;
    let mut t = vec![PAD; 3 * plane]; // NCHW, pre-filled with the grey pad
    for oy in 0..NET {
        for ox in 0..NET {
            let cx = ox as f32 - pad_x;
            let cy = oy as f32 - pad_y;
            if cx < 0.0 || cy < 0.0 || cx >= scaled_w || cy >= scaled_h {
                continue; // stays padding
            }
            let rgb = img.sample(cx / scale, cy / scale);
            // BGR order (model-color-format=0), /255.
            t[oy * NET + ox] = rgb[2] / 255.0; // plane 0 = B
            t[plane + oy * NET + ox] = rgb[1] / 255.0; // plane 1 = G
            t[2 * plane + oy * NET + ox] = rgb[0] / 255.0; // plane 2 = R
        }
    }
    (t, HeadLetterbox { scale, pad_x, pad_y })
}

/// Pick the highest-scoring **head** detection from the decoded outputs. `boxes` is row-major
/// `[cx,cy,w,h] × n` (640 space); `scores` and `classes` have length `n`. Returns
/// `(cx, cy, w, h, score)` of the best head at or above `conf`, or `None`. Pure, unit-tested.
pub fn best_head(
    boxes: &[f32],
    scores: &[f32],
    classes: &[f32],
    conf: f32,
) -> Option<(f32, f32, f32, f32, f32)> {
    let n = scores.len().min(classes.len()).min(boxes.len() / 4);
    let mut best: Option<(usize, f32)> = None;
    for i in 0..n {
        if (classes[i].round() as i32) == HEAD_CLASS && scores[i] >= conf {
            match best {
                Some((_, bs)) if bs >= scores[i] => {}
                _ => best = Some((i, scores[i])),
            }
        }
    }
    let (i, s) = best?;
    let b = &boxes[i * 4..i * 4 + 4];
    Some((b[0], b[1], b[2], b[3], s))
}

/// The loaded head detector.
pub struct HeadDetector {
    session: Session,
    conf: f32,
}

impl HeadDetector {
    /// Load `yolov11_phd_s.onnx` from `onnx_dir`. `Err` (never a panic) on a missing/bad asset.
    pub fn new(onnx_dir: &str) -> Result<Self, String> {
        let intra = onnx_intra_threads();
        let session = Session::builder()
            .and_then(configure_providers)
            .and_then(|b| b.with_intra_threads(intra))
            .and_then(|b| b.commit_from_file(format!("{onnx_dir}/yolov11_phd_s.onnx")))
            .map_err(|e| format!("load yolov11_phd_s.onnx: {e}"))?;
        let conf = std::env::var("WLBS_POSE360_HEAD_CONF")
            .ok()
            .and_then(|s| s.parse::<f32>().ok())
            .filter(|v| *v > 0.0 && *v < 1.0)
            .unwrap_or(DEFAULT_CONF);
        Ok(Self { session, conf })
    }

    /// Detect the best head box. `Ok(None)` when no head is found, `Err` (never a panic) on an ONNX
    /// error. Returns `(cx, cy, raw_side, score)` in original-image pixels (`raw_side = max(w, h)`,
    /// before the pose crop scale).
    pub fn detect(&mut self, img: &RgbImage) -> Result<Option<(f32, f32, f32, f32)>, String> {
        let (input, lb) = preprocess_640_nchw_bgr(img);
        let tensor = Tensor::from_array(([1_i64, 3, NET as i64, NET as i64], input))
            .map_err(|e| e.to_string())?;
        let out = self
            .session
            .run(ort::inputs! { "input" => tensor })
            .map_err(|e| e.to_string())?;
        let boxes = named_f32(&out, "boxes")?;
        let scores = named_f32(&out, "scores")?;
        let classes = named_f32(&out, "classes")?;
        Ok(best_head(boxes, scores, classes, self.conf).map(|(cx, cy, w, h, s)| {
            let (ix, iy, raw) = lb.box_to_image(cx, cy, w, h);
            (ix, iy, raw, s)
        }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn solid(w: usize, h: usize, rgb: [f32; 3]) -> RgbImage {
        RgbImage { w, h, px: std::iter::repeat(rgb).take(w * h).flatten().collect() }
    }

    #[test]
    fn preprocess_is_nchw_bgr_scaled() {
        // Solid red image, square so there is no padding. Plane 0 (B)=0, plane 1 (G)=0, plane 2 (R)=1.
        let img = solid(640, 640, [255.0, 0.0, 0.0]);
        let (t, lb) = preprocess_640_nchw_bgr(&img);
        assert_eq!(t.len(), 3 * 640 * 640);
        let plane = 640 * 640;
        let mid = 320 * 640 + 320;
        assert!((t[mid] - 0.0).abs() < 1e-6, "B plane should be 0 for red");
        assert!((t[plane + mid] - 0.0).abs() < 1e-6, "G plane should be 0 for red");
        assert!((t[2 * plane + mid] - 1.0).abs() < 1e-6, "R plane should be 1 for red");
        assert!((lb.scale - 1.0).abs() < 1e-6 && lb.pad_x.abs() < 1e-6 && lb.pad_y.abs() < 1e-6);
    }

    #[test]
    fn letterbox_pads_the_short_axis() {
        // 1280×720 → scale 0.5, scaled 640×360, so pad_y = (640-360)/2 = 140, pad_x = 0.
        let img = solid(1280, 720, [10.0, 20.0, 30.0]);
        let (_t, lb) = preprocess_640_nchw_bgr(&img);
        assert!((lb.scale - 0.5).abs() < 1e-6);
        assert!((lb.pad_x - 0.0).abs() < 1e-6);
        assert!((lb.pad_y - 140.0).abs() < 1e-6);
        // A box centred at tensor (320,320) size (100,160) → image (640, 360) raw_side = 160/0.5 = 320.
        let (ix, iy, raw) = lb.box_to_image(320.0, 320.0, 100.0, 160.0);
        assert!((ix - 640.0).abs() < 1e-3, "ix={ix}");
        assert!((iy - 360.0).abs() < 1e-3, "iy={iy}");
        assert!((raw - 320.0).abs() < 1e-3, "raw={raw}");
    }

    #[test]
    fn best_head_picks_top_scoring_head_only() {
        // 3 anchors: a high-score person (class 0), a mid head, a low head below conf.
        let boxes = [10.0, 10.0, 4.0, 4.0, 20.0, 20.0, 6.0, 6.0, 30.0, 30.0, 8.0, 8.0];
        let scores = [0.9, 0.5, 0.1];
        let classes = [0.0, 1.0, 1.0];
        let r = best_head(&boxes, &scores, &classes, 0.25).unwrap();
        assert_eq!(r, (20.0, 20.0, 6.0, 6.0, 0.5)); // the person is ignored; low head below conf
    }

    #[test]
    fn best_head_none_when_no_head_above_conf() {
        let boxes = [10.0, 10.0, 4.0, 4.0];
        assert!(best_head(&boxes, &[0.9], &[0.0], 0.25).is_none()); // only a person
        assert!(best_head(&boxes, &[0.2], &[1.0], 0.25).is_none()); // head below conf
    }

    #[test]
    fn best_head_prefers_higher_of_two_heads() {
        let boxes = [10.0, 10.0, 4.0, 4.0, 20.0, 20.0, 6.0, 6.0];
        let r = best_head(&boxes, &[0.4, 0.8], &[1.0, 1.0], 0.25).unwrap();
        assert_eq!(r.4, 0.8);
        assert_eq!(r.0, 20.0);
    }
}
