//! BlazeFace short-range decode + weighted NMS.
//!
//! Turns the detector's raw tensors (`regressors` [896×16], `classificators` [896×1]) into face
//! detections, ported from MediaPipe's `TensorsToDetectionsCalculator` (short-range options) +
//! `NonMaxSuppressionCalculator` (weighted IoU). Coordinates are normalised to the 128² detector
//! input; the caller maps them back through the letterbox to original-image coordinates.
//!
//! Short-range decode options: `num_coords 16`, `x/y/w/h_scale 128`, `keypoint_coord_offset 4`,
//! `num_keypoints 6`, `reverse_output_order true` (box = x,y,w,h; keypoints = x,y pairs),
//! `sigmoid_score true`, `score_clipping_thresh 100`. Keypoint order: 0 right-eye, 1 left-eye,
//! 2 nose-tip, 3 mouth, 4 right-ear, 5 left-ear.

use crate::anchors::Anchor;

const NUM_COORDS: usize = 16;
const SCALE: f32 = 128.0;
const KP_OFFSET: usize = 4;
pub const NUM_KEYPOINTS: usize = 6;
const SCORE_CLIP: f32 = 100.0;
const MIN_SCORE: f32 = 0.5;
const MIN_SUPPRESSION_IOU: f32 = 0.3;

/// A face detection in normalised detector-input coordinates ([0,1] over the 128² square).
#[derive(Debug, Clone)]
pub struct Detection {
    pub score: f32,
    pub xmin: f32,
    pub ymin: f32,
    pub xmax: f32,
    pub ymax: f32,
    /// 6 keypoints (x,y), order: right-eye, left-eye, nose, mouth, right-ear, left-ear.
    pub keypoints: [(f32, f32); NUM_KEYPOINTS],
}

impl Detection {
    fn area(&self) -> f32 {
        ((self.xmax - self.xmin).max(0.0)) * ((self.ymax - self.ymin).max(0.0))
    }
    fn iou(&self, o: &Detection) -> f32 {
        let ix0 = self.xmin.max(o.xmin);
        let iy0 = self.ymin.max(o.ymin);
        let ix1 = self.xmax.min(o.xmax);
        let iy1 = self.ymax.min(o.ymax);
        let iw = (ix1 - ix0).max(0.0);
        let ih = (iy1 - iy0).max(0.0);
        let inter = iw * ih;
        let denom = self.area() + o.area() - inter;
        if denom <= 0.0 {
            0.0
        } else {
            inter / denom
        }
    }
}

fn sigmoid(x: f32) -> f32 {
    1.0 / (1.0 + (-x).exp())
}

/// Decode the raw tensors into scored detections (score ≥ [`MIN_SCORE`]), pre-NMS.
pub fn decode(regressors: &[f32], scores: &[f32], anchors: &[Anchor]) -> Vec<Detection> {
    assert_eq!(regressors.len(), anchors.len() * NUM_COORDS);
    assert_eq!(scores.len(), anchors.len());
    let mut out = Vec::new();
    for (i, a) in anchors.iter().enumerate() {
        let s = sigmoid(scores[i].clamp(-SCORE_CLIP, SCORE_CLIP));
        if s < MIN_SCORE {
            continue;
        }
        let r = &regressors[i * NUM_COORDS..(i + 1) * NUM_COORDS];
        // reverse_output_order: box = (x_center, y_center, w, h); anchor w=h=1 (fixed_anchor_size).
        let xc = r[0] / SCALE + a.x_center;
        let yc = r[1] / SCALE + a.y_center;
        let w = r[2] / SCALE;
        let h = r[3] / SCALE;
        let mut keypoints = [(0.0f32, 0.0f32); NUM_KEYPOINTS];
        for (k, kp) in keypoints.iter_mut().enumerate() {
            let kx = r[KP_OFFSET + k * 2] / SCALE + a.x_center;
            let ky = r[KP_OFFSET + k * 2 + 1] / SCALE + a.y_center;
            *kp = (kx, ky);
        }
        out.push(Detection {
            score: s,
            xmin: xc - w / 2.0,
            ymin: yc - h / 2.0,
            xmax: xc + w / 2.0,
            ymax: yc + h / 2.0,
            keypoints,
        });
    }
    out
}

/// Weighted NMS (MediaPipe's weighted algorithm): for each cluster (IoU ≥ 0.3 with the current
/// top), emit a score-weighted average of the box and keypoints. Returns detections sorted by score.
pub fn weighted_nms(mut dets: Vec<Detection>) -> Vec<Detection> {
    dets.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal));
    let mut kept = Vec::new();
    let mut used = vec![false; dets.len()];
    for i in 0..dets.len() {
        if used[i] {
            continue;
        }
        // gather the cluster overlapping the (highest-score, unused) anchor det[i]
        let mut cluster = vec![i];
        used[i] = true;
        for j in (i + 1)..dets.len() {
            if !used[j] && dets[i].iou(&dets[j]) >= MIN_SUPPRESSION_IOU {
                used[j] = true;
                cluster.push(j);
            }
        }
        // score-weighted average over the cluster's location data
        let wsum: f32 = cluster.iter().map(|&k| dets[k].score).sum();
        let mut acc = Detection {
            score: dets[i].score, // keep the max score as the detection's confidence
            xmin: 0.0,
            ymin: 0.0,
            xmax: 0.0,
            ymax: 0.0,
            keypoints: [(0.0, 0.0); NUM_KEYPOINTS],
        };
        for &k in &cluster {
            let wgt = dets[k].score / wsum;
            acc.xmin += dets[k].xmin * wgt;
            acc.ymin += dets[k].ymin * wgt;
            acc.xmax += dets[k].xmax * wgt;
            acc.ymax += dets[k].ymax * wgt;
            for (dst, src) in acc.keypoints.iter_mut().zip(dets[k].keypoints.iter()) {
                dst.0 += src.0 * wgt;
                dst.1 += src.1 * wgt;
            }
        }
        kept.push(acc);
    }
    kept
}

/// Full detector postprocess: decode + weighted NMS → detections (score-desc). Empty if no face.
pub fn detections(regressors: &[f32], scores: &[f32], anchors: &[Anchor]) -> Vec<Detection> {
    weighted_nms(decode(regressors, scores, anchors))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn det(score: f32, cx: f32, cy: f32, s: f32) -> Detection {
        Detection {
            score,
            xmin: cx - s,
            ymin: cy - s,
            xmax: cx + s,
            ymax: cy + s,
            keypoints: [(cx, cy); NUM_KEYPOINTS],
        }
    }

    #[test]
    fn weighted_nms_merges_overlapping() {
        let a = det(0.9, 0.5, 0.5, 0.1);
        let b = det(0.8, 0.51, 0.5, 0.1); // heavy overlap with a
        let c = det(0.95, 0.1, 0.1, 0.05); // far away
        let out = weighted_nms(vec![a, b, c]);
        assert_eq!(out.len(), 2, "two clusters: the a/b pair and c");
        // top cluster score is the max (0.9), box is the score-weighted blend of a and b
        let top = out.iter().find(|d| d.score > 0.85 && d.xmin > 0.3).unwrap();
        assert!(top.xmin > 0.39 && top.xmin < 0.42);
    }

    #[test]
    fn sigmoid_threshold_filters() {
        let anchors = vec![Anchor { x_center: 0.5, y_center: 0.5 }; 2];
        let regr = vec![0.0f32; 2 * NUM_COORDS];
        // logit 0 -> sigmoid 0.5 (>= MIN_SCORE 0.5, kept); logit -5 -> ~0.007 (dropped)
        let scores = vec![0.0, -5.0];
        let d = decode(&regr, &scores, &anchors);
        assert_eq!(d.len(), 1);
    }
}
