//! SSD anchor generation for MediaPipe's BlazeFace short-range (128²) detector.
//!
//! A faithful port of MediaPipe's `SsdAnchorsCalculator` for the short-range face config
//! (`min_scale 0.1484375`, `max_scale 0.75`, `strides [8,16,16,16]`, `num_layers 4`,
//! `fixed_anchor_size true`). It yields exactly 896 anchors (16·16·2 + 8·8·6) in the same order the
//! detector's `regressors`/`classificators` rows are indexed by, so the decode lines up anchor-for-row.
//!
//! With `fixed_anchor_size` every anchor's width/height is 1.0, so only the (x,y) centre varies; the
//! decode scales raw offsets by that unit size (see [`crate::detector`]).

/// One anchor: normalised centre in the 128² input. Width/height are 1.0 (fixed_anchor_size).
#[derive(Debug, Clone, Copy)]
pub struct Anchor {
    pub x_center: f32,
    pub y_center: f32,
}

const INPUT: f32 = 128.0;
const MIN_SCALE: f32 = 0.1484375;
const MAX_SCALE: f32 = 0.75;
const OFFSET: f32 = 0.5;
const STRIDES: [i32; 4] = [8, 16, 16, 16];

fn calculate_scale(min_scale: f32, max_scale: f32, stride_index: i32, num_strides: i32) -> f32 {
    if num_strides == 1 {
        (min_scale + max_scale) * 0.5
    } else {
        min_scale + (max_scale - min_scale) * stride_index as f32 / (num_strides as f32 - 1.0)
    }
}

/// Generate the 896 short-range anchors, in MediaPipe's emission order.
pub fn short_range_anchors() -> Vec<Anchor> {
    let num_strides = STRIDES.len() as i32;
    let mut anchors = Vec::with_capacity(896);
    let mut layer_id = 0usize;
    while layer_id < STRIDES.len() {
        // Count anchors-per-cell by merging consecutive layers that share this stride, mirroring
        // MediaPipe (so the three stride-16 layers collapse onto one 8×8 feature map, 6 anchors/cell).
        let mut anchors_per_cell = 0usize;
        let mut last = layer_id;
        while last < STRIDES.len() && STRIDES[last] == STRIDES[layer_id] {
            // aspect_ratios = [1.0]  -> one anchor
            anchors_per_cell += 1;
            // interpolated_scale_aspect_ratio = 1.0 -> one extra interpolated-scale anchor
            // (scale value is irrelevant here because fixed_anchor_size makes w=h=1).
            let _scale = calculate_scale(MIN_SCALE, MAX_SCALE, last as i32, num_strides);
            anchors_per_cell += 1;
            last += 1;
        }
        let stride = STRIDES[layer_id];
        let feature_map_height = ((INPUT as i32 + stride - 1) / stride) as usize; // ceil(128/stride)
        let feature_map_width = feature_map_height; // square input
        for y in 0..feature_map_height {
            let y_center = (y as f32 + OFFSET) / feature_map_height as f32;
            for x in 0..feature_map_width {
                let x_center = (x as f32 + OFFSET) / feature_map_width as f32;
                for _ in 0..anchors_per_cell {
                    anchors.push(Anchor { x_center, y_center });
                }
            }
        }
        layer_id = last;
    }
    anchors
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn yields_896_anchors() {
        let a = short_range_anchors();
        assert_eq!(a.len(), 896, "short-range must be 16*16*2 + 8*8*6 = 896");
        // all centres within (0,1)
        for an in &a {
            assert!(an.x_center > 0.0 && an.x_center < 1.0);
            assert!(an.y_center > 0.0 && an.y_center < 1.0);
        }
        // first cell of the stride-8 layer: centre (0.5/16, 0.5/16), duplicated (2 anchors/cell).
        assert!((a[0].x_center - 0.5 / 16.0).abs() < 1e-6);
        assert!((a[0].y_center - 0.5 / 16.0).abs() < 1e-6);
        assert_eq!(a[0].x_center, a[1].x_center);
        assert_eq!(a[0].y_center, a[1].y_center);
    }
}
