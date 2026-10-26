//! Face-detection → FaceMesh ROI: MediaPipe's `face_detection_..._to_roi` subgraph.
//!
//! `DetectionsToRectsCalculator` (rotate so the right-eye→left-eye line hits the target angle 0) +
//! `RectTransformationCalculator` (`scale 1.5`, `square_long`). The resulting rotated square (in
//! original-image pixels) is what [`crate::image_util::RotatedRect`] crops to 256² for the net.

use crate::detector::Detection;
use crate::image_util::{Letterbox, RotatedRect};

/// Detection-to-ROI scale (MediaPipe face_landmark: `scale_x = scale_y = 1.5`, `square_long = true`).
const ROI_SCALE: f32 = 1.5;
/// Keypoint indices used to orient the rect: 0 = right eye, 1 = left eye.
const RIGHT_EYE: usize = 0;
const LEFT_EYE: usize = 1;

/// Normalise radians to (-π, π].
fn normalize_radians(mut a: f32) -> f32 {
    use std::f32::consts::PI;
    a -= 2.0 * PI * ((a + PI) / (2.0 * PI)).floor();
    a
}

/// Build the FaceMesh ROI (rotated square, original-image pixels) from a detection + the letterbox
/// mapping back to the source image.
pub fn detection_to_rect(det: &Detection, lb: &Letterbox) -> RotatedRect {
    // box corners -> image pixels
    let (x0, y0) = lb.to_image_px(det.xmin, det.ymin);
    let (x1, y1) = lb.to_image_px(det.xmax, det.ymax);
    let cx = (x0 + x1) / 2.0;
    let cy = (y0 + y1) / 2.0;
    let box_w = x1 - x0;
    let box_h = y1 - y0;

    // rotation from the eye line (ComputeRotation, target_angle 0)
    let (rex, rey) = lb.to_image_px(det.keypoints[RIGHT_EYE].0, det.keypoints[RIGHT_EYE].1);
    let (lex, ley) = lb.to_image_px(det.keypoints[LEFT_EYE].0, det.keypoints[LEFT_EYE].1);
    let theta = normalize_radians(0.0 - (-(ley - rey)).atan2(lex - rex));

    // square_long + scale 1.5
    let side = box_w.max(box_h) * ROI_SCALE;
    RotatedRect { cx, cy, side, theta }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::detector::{Detection, NUM_KEYPOINTS};

    fn identity_lb() -> Letterbox {
        // scale 1, no pad, 128×128 "image" so normalized*128 == pixels
        Letterbox { scale: 1.0, pad_x: 0.0, pad_y: 0.0, img_w: 128.0, img_h: 128.0 }
    }

    #[test]
    fn upright_face_zero_rotation() {
        let mut kp = [(0.0f32, 0.0f32); NUM_KEYPOINTS];
        kp[RIGHT_EYE] = (0.4, 0.4); // level eyes -> theta 0
        kp[LEFT_EYE] = (0.6, 0.4);
        let d = Detection { score: 1.0, xmin: 0.3, ymin: 0.3, xmax: 0.7, ymax: 0.7, keypoints: kp };
        let r = detection_to_rect(&d, &identity_lb());
        assert!(r.theta.abs() < 1e-5, "level eyes -> no rotation, got {}", r.theta);
        // box is 0.4*128 = 51.2 px square; side = 51.2 * 1.5
        assert!((r.side - 51.2 * 1.5).abs() < 1e-3);
        assert!((r.cx - 64.0).abs() < 1e-4 && (r.cy - 64.0).abs() < 1e-4);
    }

    #[test]
    fn tilted_eyes_give_rotation() {
        let mut kp = [(0.0f32, 0.0f32); NUM_KEYPOINTS];
        kp[RIGHT_EYE] = (0.4, 0.45);
        kp[LEFT_EYE] = (0.6, 0.35); // left eye higher -> head tilted
        let d = Detection { score: 1.0, xmin: 0.3, ymin: 0.3, xmax: 0.7, ymax: 0.7, keypoints: kp };
        let r = detection_to_rect(&d, &identity_lb());
        assert!(r.theta.abs() > 1e-3, "tilted eyes must yield a nonzero rotation");
    }
}
