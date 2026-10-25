//! Image preprocessing: the detector letterbox and the FaceMesh rotated-rect crop, plus the inverse
//! landmark projection — the MediaPipe `ImageToTensorCalculator` / `LandmarkProjectionCalculator`
//! geometry, reproduced so the Rust pipeline frames the face exactly as the sidecar did.

/// An RGB image as planar-free interleaved f32 in [0,255], row-major, `w`×`h`.
pub struct RgbImage {
    pub w: usize,
    pub h: usize,
    /// length `w*h*3`, channel order R,G,B.
    pub px: Vec<f32>,
}

impl RgbImage {
    /// Bilinear sample at (x,y) in pixel coordinates; out-of-bounds clamps to the edge (border
    /// replicate). Returns (r,g,b).
    #[inline]
    pub fn sample(&self, x: f32, y: f32) -> [f32; 3] {
        let x = x.clamp(0.0, (self.w - 1) as f32);
        let y = y.clamp(0.0, (self.h - 1) as f32);
        let x0 = x.floor() as usize;
        let y0 = y.floor() as usize;
        let x1 = (x0 + 1).min(self.w - 1);
        let y1 = (y0 + 1).min(self.h - 1);
        let fx = x - x0 as f32;
        let fy = y - y0 as f32;
        let mut out = [0.0f32; 3];
        for (c, o) in out.iter_mut().enumerate() {
            let p00 = self.px[(y0 * self.w + x0) * 3 + c];
            let p01 = self.px[(y0 * self.w + x1) * 3 + c];
            let p10 = self.px[(y1 * self.w + x0) * 3 + c];
            let p11 = self.px[(y1 * self.w + x1) * 3 + c];
            let top = p00 + (p01 - p00) * fx;
            let bot = p10 + (p11 - p10) * fx;
            *o = top + (bot - top) * fy;
        }
        out
    }
}

/// Letterbox transform: how the 128² detector tensor maps back to the original image. The content
/// occupies `[pad_x, pad_x+scaled_w) × [pad_y, pad_y+scaled_h)` (in 128-tensor pixels) and was scaled
/// from the original by `scale = 128/max(w,h)`.
pub struct Letterbox {
    pub scale: f32,
    pub pad_x: f32,
    pub pad_y: f32,
    pub img_w: f32,
    pub img_h: f32,
}

/// Build the 128×128 detector input (NHWC, normalised to [-1,1]) by aspect-preserving letterbox with
/// zero padding, and return the mapping back to original pixels.
pub fn letterbox_128(img: &RgbImage) -> (Vec<f32>, Letterbox) {
    const S: usize = 128;
    let scale = S as f32 / img.w.max(img.h) as f32;
    let scaled_w = img.w as f32 * scale;
    let scaled_h = img.h as f32 * scale;
    let pad_x = (S as f32 - scaled_w) / 2.0;
    let pad_y = (S as f32 - scaled_h) / 2.0;
    let mut t = vec![-1.0f32; S * S * 3]; // zero pixel -> -1 after [-1,1] normalisation
    for oy in 0..S {
        for ox in 0..S {
            let cx = ox as f32 - pad_x;
            let cy = oy as f32 - pad_y;
            if cx < 0.0 || cy < 0.0 || cx >= scaled_w || cy >= scaled_h {
                continue; // stays padding (-1)
            }
            let src_x = cx / scale;
            let src_y = cy / scale;
            let rgb = img.sample(src_x, src_y);
            for c in 0..3 {
                t[(oy * S + ox) * 3 + c] = rgb[c] / 127.5 - 1.0;
            }
        }
    }
    (t, Letterbox { scale, pad_x, pad_y, img_w: img.w as f32, img_h: img.h as f32 })
}

impl Letterbox {
    /// Map a point given in normalised [0,1] detector coordinates back to original image pixels.
    pub fn to_image_px(&self, nx: f32, ny: f32) -> (f32, f32) {
        let tx = nx * 128.0 - self.pad_x;
        let ty = ny * 128.0 - self.pad_y;
        (tx / self.scale, ty / self.scale)
    }
}

/// A rotated square crop region in original-image pixel coordinates.
pub struct RotatedRect {
    pub cx: f32,
    pub cy: f32,
    pub side: f32,
    pub theta: f32,
}

impl RotatedRect {
    /// Render the 256×256 FaceMesh input (NHWC, normalised to [0,1]) by sampling the source along the
    /// rotated rect. Output pixel (ox,oy) ↔ rect-local (ox/256-0.5, oy/256-0.5)·side, rotated by θ.
    pub fn crop_256(&self, img: &RgbImage) -> Vec<f32> {
        const S: usize = 256;
        let (sin, cos) = self.theta.sin_cos();
        let mut t = vec![0.0f32; S * S * 3];
        for oy in 0..S {
            for ox in 0..S {
                let xr = ((ox as f32 + 0.5) / S as f32 - 0.5) * self.side;
                let yr = ((oy as f32 + 0.5) / S as f32 - 0.5) * self.side;
                let sx = self.cx + xr * cos - yr * sin;
                let sy = self.cy + xr * sin + yr * cos;
                let rgb = img.sample(sx, sy);
                for c in 0..3 {
                    t[(oy * S + ox) * 3 + c] = rgb[c] / 255.0;
                }
            }
        }
        t
    }

    /// Project a FaceMesh landmark (x,y,z in 256-input pixel units) back to original image pixels,
    /// with z scaled by the rect side (the LandmarkProjectionCalculator convention) — exactly the
    /// `[L.x*w, L.y*h, L.z*w]` the Kabsch solve consumes.
    pub fn project_landmark(&self, lx: f32, ly: f32, lz: f32) -> [f64; 3] {
        let (sin, cos) = self.theta.sin_cos();
        let xr = (lx / 256.0 - 0.5) * self.side;
        let yr = (ly / 256.0 - 0.5) * self.side;
        let img_x = self.cx + xr * cos - yr * sin;
        let img_y = self.cy + xr * sin + yr * cos;
        let img_z = (lz / 256.0) * self.side;
        [img_x as f64, img_y as f64, img_z as f64]
    }
}
