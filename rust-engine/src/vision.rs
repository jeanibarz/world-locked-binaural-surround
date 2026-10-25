//! The head-pose source: `vision-client`'s ONNX pipeline producing a live yaw that drives the
//! world-lock. Zero Python — BlazeFace + FaceMesh + Kabsch, the body validated in slice 1.
//!
//! Two frame sources (both honest, no camera-device conflict with the owner's running engine):
//!   * **MJPEG** — pull JPEG frames from an MJPEG URL (e.g. the cockpit's `/camera`), the same
//!     passive tap the R-S1 spike used, so the owner's live face drives the tracker.
//!   * **image** — a single face image looped (an in-process smoke of the vision→pose wire).
//!
//! The product's default frame source is a direct webcam (nokhwa); it is a drop-in behind this same
//! yaw-updating loop and is left out here only because the owner's camera is currently held by their
//! Python engine, which would make a live device-open conflict.
#![cfg(windows)]

use std::io::Read;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

use control_plane::Cockpit;
use vision_client::image_util::RgbImage;
use vision_client::pipeline::{decode_image, Pipeline};

fn now_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis() as u64).unwrap_or(0)
}

/// Shared, recenter-able head yaw (deg). The first valid pose defines neutral (0). Also carries
/// tracker liveness (last-update timestamp) and the camera resolution, for cockpit telemetry.
pub struct PoseState {
    pub yaw: Mutex<f64>,
    yaw0: Mutex<Option<f64>>,
    /// Epoch-millis of the last pose update, or 0 if no pose has been produced yet.
    last_update: AtomicU64,
    /// The active camera resolution, once a camera stream is open.
    cam_res: Mutex<Option<(u32, u32)>>,
}

impl PoseState {
    pub fn new() -> Arc<Self> {
        Arc::new(Self {
            yaw: Mutex::new(0.0),
            yaw0: Mutex::new(None),
            last_update: AtomicU64::new(0),
            cam_res: Mutex::new(None),
        })
    }
    fn update_raw(&self, raw_yaw: f64) {
        let mut y0 = self.yaw0.lock().unwrap();
        if y0.is_none() {
            *y0 = Some(raw_yaw); // first pose = neutral
        }
        *self.yaw.lock().unwrap() = raw_yaw - y0.unwrap();
        self.last_update.store(now_ms(), Ordering::Relaxed);
    }

    /// Fold the current offset into neutral so the direction the head faces right now reads as 0°
    /// (the cockpit Recenter control). A no-op before the first pose. Fixes BUG-09.
    pub fn recenter(&self) {
        let mut y0 = self.yaw0.lock().unwrap();
        let mut y = self.yaw.lock().unwrap();
        if let Some(cur0) = *y0 {
            *y0 = Some(cur0 + *y);
            *y = 0.0;
        }
    }

    /// Has a pose ever been produced?
    pub fn is_tracking(&self) -> bool {
        self.last_update.load(Ordering::Relaxed) != 0
    }

    /// Milliseconds since the last pose update, or `None` if none yet.
    pub fn millis_since_update(&self) -> Option<u64> {
        match self.last_update.load(Ordering::Relaxed) {
            0 => None,
            t => Some(now_ms().saturating_sub(t)),
        }
    }

    /// The active camera resolution, if a camera stream is open.
    pub fn cam_res(&self) -> Option<(u32, u32)> {
        *self.cam_res.lock().unwrap()
    }
    fn set_cam_res(&self, w: u32, h: u32) {
        *self.cam_res.lock().unwrap() = Some((w, h));
    }
}

/// Enumerate the local cameras (index, human name) for the cockpit picker. Empty on error / none.
pub fn enumerate_cameras() -> Vec<(u32, String)> {
    use nokhwa::query;
    use nokhwa::utils::{ApiBackend, CameraIndex};
    match query(ApiBackend::Auto) {
        Ok(list) => list
            .iter()
            .filter_map(|c| match c.index() {
                CameraIndex::Index(i) => Some((*i, c.human_name())),
                CameraIndex::String(_) => None,
            })
            .collect(),
        Err(e) => {
            eprintln!("VISION_ERROR camera query: {e}");
            Vec::new()
        }
    }
}

/// Open local camera `index`, run the pose pipeline on ~every `stride`-th frame, update `pose`.
/// Drop-in behind the same yaw loop as the MJPEG path, fed by direct webcam capture (nokhwa).
pub fn spawn_camera_pose(
    index: u32,
    onnx_dir: String,
    canonical: String,
    stride: usize,
    pose: Arc<PoseState>,
    running: Arc<AtomicBool>,
    cockpit: Option<Arc<Cockpit>>,
) {
    std::thread::spawn(move || {
        use nokhwa::pixel_format::RgbFormat;
        use nokhwa::utils::{CameraIndex, RequestedFormat, RequestedFormatType};
        use nokhwa::Camera;

        let mut pipe = match Pipeline::new(&onnx_dir, &canonical) {
            Ok(p) => p,
            Err(e) => {
                eprintln!("VISION_ERROR pipeline: {e}");
                return;
            }
        };
        let fmt = RequestedFormat::new::<RgbFormat>(RequestedFormatType::AbsoluteHighestFrameRate);
        let mut cam = match Camera::new(CameraIndex::Index(index), fmt) {
            Ok(c) => c,
            Err(e) => {
                eprintln!("VISION_ERROR camera open (index {index}): {e}");
                return;
            }
        };
        if let Err(e) = cam.open_stream() {
            eprintln!("VISION_ERROR camera stream: {e}");
            return;
        }
        let res = cam.resolution();
        pose.set_cam_res(res.width(), res.height());
        println!("POSE camera index={index} {}x{}", res.width(), res.height());

        // The cockpit's `/camera` preview is fed by re-encoding the decoded RGB frame to JPEG and
        // handing it to `Cockpit::set_frame` (BUG-07 — before, only the test bin ever did this, so the
        // preview was always blank). Throttled to ~12 fps so the preview never competes with pose.
        let mut last_preview = std::time::Instant::now() - std::time::Duration::from_secs(1);
        let (mut ok, mut miss, mut n) = (0u64, 0u64, 0usize);
        while running.load(Ordering::Relaxed) {
            let frame = match cam.frame() {
                Ok(f) => f,
                Err(e) => {
                    eprintln!("VISION_ERROR frame: {e}");
                    std::thread::sleep(std::time::Duration::from_millis(100));
                    continue;
                }
            };
            n += 1;
            if !n.is_multiple_of(stride) {
                continue; // sub-sample: pose rate < camera rate (NFR-4b decoupling)
            }
            let decoded = match frame.decode_image::<RgbFormat>() {
                Ok(d) => d,
                Err(_) => continue,
            };
            if let Some(cp) = &cockpit {
                if last_preview.elapsed() >= std::time::Duration::from_millis(80) {
                    if let Some(jpeg) = encode_jpeg(decoded.as_raw(), decoded.width(), decoded.height()) {
                        cp.set_frame(jpeg);
                    }
                    last_preview = std::time::Instant::now();
                }
            }
            let img = RgbImage {
                w: decoded.width() as usize,
                h: decoded.height() as usize,
                px: decoded.as_raw().iter().map(|&b| b as f32).collect(),
            };
            match pipe.estimate(&img) {
                Ok(Some(est)) => {
                    ok += 1;
                    pose.update_raw(est.pose.0);
                }
                _ => miss += 1,
            }
            if (ok + miss).is_multiple_of(30) {
                eprintln!("VISION ok={ok} miss={miss} yaw={:.1}", *pose.yaw.lock().unwrap());
            }
        }
    });
}

/// Encode an interleaved RGB8 buffer to a baseline JPEG for the cockpit preview. `None` on any
/// encoder error (the preview just skips that frame).
fn encode_jpeg(rgb: &[u8], w: u32, h: u32) -> Option<Vec<u8>> {
    use image::codecs::jpeg::JpegEncoder;
    use image::ExtendedColorType;
    if rgb.len() < (w as usize * h as usize * 3) {
        return None;
    }
    let mut out = Vec::with_capacity(64 * 1024);
    let mut enc = JpegEncoder::new_with_quality(&mut out, 70);
    enc.encode(rgb, w, h, ExtendedColorType::Rgb8).ok()?;
    Some(out)
}

/// Extract complete JPEGs (SOI 0xFFD8 … EOI 0xFFD9) from a growing byte buffer, returning the
/// frames found and leaving the trailing partial in `buf`.
fn drain_jpegs(buf: &mut Vec<u8>) -> Vec<Vec<u8>> {
    let mut out = Vec::new();
    loop {
        let soi = buf.windows(2).position(|w| w == [0xFF, 0xD8]);
        let Some(s) = soi else { break };
        let eoi = buf[s + 2..].windows(2).position(|w| w == [0xFF, 0xD9]).map(|e| s + 2 + e + 2);
        let Some(e) = eoi else {
            if s > 0 {
                buf.drain(..s); // drop leading junk before an incomplete frame
            }
            break;
        };
        out.push(buf[s..e].to_vec());
        buf.drain(..e);
    }
    out
}

/// Pull MJPEG frames from `url`, run the pose pipeline on ~every `stride`-th frame, update `pose`.
pub fn spawn_mjpeg_pose(
    url: String,
    onnx_dir: String,
    canonical: String,
    stride: usize,
    pose: Arc<PoseState>,
    running: Arc<AtomicBool>,
) {
    std::thread::spawn(move || {
        let mut pipe = match Pipeline::new(&onnx_dir, &canonical) {
            Ok(p) => p,
            Err(e) => {
                eprintln!("VISION_ERROR pipeline: {e}");
                return;
            }
        };
        while running.load(Ordering::Relaxed) {
            let resp = match ureq::get(&url).call() {
                Ok(r) => r,
                Err(e) => {
                    eprintln!("VISION_ERROR mjpeg connect: {e}");
                    std::thread::sleep(std::time::Duration::from_millis(500));
                    continue;
                }
            };
            let mut reader = resp.into_reader();
            let mut buf = Vec::with_capacity(1 << 18);
            let mut chunk = [0u8; 8192];
            let mut n_frame = 0usize;
            let (mut ok, mut miss) = (0u64, 0u64);
            while running.load(Ordering::Relaxed) {
                let got = match reader.read(&mut chunk) {
                    Ok(0) | Err(_) => break,
                    Ok(n) => n,
                };
                buf.extend_from_slice(&chunk[..got]);
                for jpeg in drain_jpegs(&mut buf) {
                    n_frame += 1;
                    if !n_frame.is_multiple_of(stride) {
                        continue; // sub-sample: pose rate < camera rate (NFR-4b decoupling)
                    }
                    if let Ok(img) = decode_image(&jpeg) {
                        match pipe.estimate(&img) {
                            Ok(Some(est)) => {
                                ok += 1;
                                pose.update_raw(est.pose.0);
                            }
                            _ => miss += 1,
                        }
                        if (ok + miss).is_multiple_of(20) {
                            eprintln!("VISION ok={ok} miss={miss} yaw={:.1}", *pose.yaw.lock().unwrap());
                        }
                    }
                }
            }
        }
    });
}

/// Loop a single face image through the pipeline (in-process wire smoke; yaw is static).
pub fn spawn_image_pose(
    path: String,
    onnx_dir: String,
    canonical: String,
    pose: Arc<PoseState>,
    running: Arc<AtomicBool>,
) {
    std::thread::spawn(move || {
        let mut pipe = match Pipeline::new(&onnx_dir, &canonical) {
            Ok(p) => p,
            Err(e) => {
                eprintln!("VISION_ERROR pipeline: {e}");
                return;
            }
        };
        let bytes = match std::fs::read(&path) {
            Ok(b) => b,
            Err(e) => {
                eprintln!("VISION_ERROR read {path}: {e}");
                return;
            }
        };
        let img = match decode_image(&bytes) {
            Ok(i) => i,
            Err(e) => {
                eprintln!("VISION_ERROR decode: {e}");
                return;
            }
        };
        while running.load(Ordering::Relaxed) {
            if let Ok(Some(est)) = pipe.estimate(&img) {
                pose.update_raw(est.pose.0);
                eprintln!("VISION image yaw={:.2}", est.pose.0);
            }
            std::thread::sleep(std::time::Duration::from_millis(100));
        }
    });
}
