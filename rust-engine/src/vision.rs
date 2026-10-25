//! The head-pose source: `vision-client`'s ONNX pipeline producing a live yaw that drives the
//! world-lock. Zero Python — BlazeFace + FaceMesh + Kabsch, the body validated in slice 1.
//!
//! Three frame sources (the product default is the first):
//!   * **camera** — direct webcam capture (nokhwa, OS-native backend). The product's default pose
//!     source; it also feeds the cockpit `/camera` preview.
//!   * **MJPEG** — pull JPEG frames from an MJPEG URL (e.g. a phone/IP camera), the passive tap the
//!     R-S1 spike used.
//!   * **image** — a single face image looped (an in-process smoke of the vision→pose wire).
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
    /// A camera/pipeline open failure, so the cockpit can say "camera unavailable" instead of
    /// showing a permanently-searching tracker (BUG-10).
    cam_error: Mutex<Option<String>>,
}

impl PoseState {
    pub fn new() -> Arc<Self> {
        Arc::new(Self {
            yaw: Mutex::new(0.0),
            yaw0: Mutex::new(None),
            last_update: AtomicU64::new(0),
            cam_res: Mutex::new(None),
            cam_error: Mutex::new(None),
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

    /// The camera/pipeline open error, if the pose source failed to start (BUG-10).
    pub fn cam_error(&self) -> Option<String> {
        self.cam_error.lock().unwrap().clone()
    }
    fn set_cam_error(&self, e: String) {
        *self.cam_error.lock().unwrap() = Some(e);
    }
    fn clear_cam_error(&self) {
        *self.cam_error.lock().unwrap() = None;
    }
}

/// Enumerate the local cameras (index, human name) for the cockpit picker. Empty on error / none.
/// String-indexed cameras (rare on the Windows MediaFoundation backend) are skipped with a log line
/// rather than dropped silently; selecting them would need a `CameraIndex::String` path (BUG-34).
///
/// The nokhwa query runs on a short-lived joined thread so MediaFoundation's `CoInitialize` happens
/// there, not on the engine's main thread — otherwise it would pin the main thread's COM apartment
/// before the engine's own `init_com()`/`initialize_mta()` runs, aborting startup with
/// `RPC_E_CHANGED_MODE` (BUG-35). The child thread dies right after, releasing its apartment.
pub fn enumerate_cameras() -> Vec<(u32, String)> {
    std::thread::spawn(|| {
        use nokhwa::query;
        use nokhwa::utils::{ApiBackend, CameraIndex};
        match query(ApiBackend::Auto) {
            Ok(list) => list
                .iter()
                .filter_map(|c| match c.index() {
                    CameraIndex::Index(i) => Some((*i, c.human_name())),
                    CameraIndex::String(s) => {
                        eprintln!("VISION camera skipped (string index {s:?}): {}", c.human_name());
                        None
                    }
                })
                .collect(),
            Err(e) => {
                eprintln!("VISION_ERROR camera query: {e}");
                Vec::new()
            }
        }
    })
    .join()
    .unwrap_or_else(|_| {
        eprintln!("VISION_ERROR camera-query thread panicked");
        Vec::new()
    })
}

/// Open camera `index` at the requested resolution/fps (0 = a sane default), run the pose pipeline
/// on ~every `stride`-th frame, update `pose`, and feed the cockpit preview. Stride is clamped to
/// ≥1 by the caller. Reopens the device if it is unplugged, and releases it on exit.
#[allow(clippy::too_many_arguments)]
pub fn spawn_camera_pose(
    index: u32,
    onnx_dir: String,
    canonical: String,
    stride: usize,
    req_w: u32,
    req_h: u32,
    req_fps: u32,
    pose: Arc<PoseState>,
    running: Arc<AtomicBool>,
    cockpit: Option<Arc<Cockpit>>,
) {
    use nokhwa::pixel_format::RgbFormat;

    std::thread::spawn(move || {
        // The pipeline (ONNX nets + canonical model) opens once; a bad asset is a clean error, not a
        // panic (BUG-11), surfaced to the cockpit as "camera unavailable" (BUG-10).
        let mut pipe = match Pipeline::new(&onnx_dir, &canonical) {
            Ok(p) => p,
            Err(e) => {
                pose.set_cam_error(format!("pipeline: {e}"));
                eprintln!("VISION_ERROR pipeline: {e}");
                return;
            }
        };

        // Reused across frames so we don't allocate (and free) a full-frame f32 buffer every frame
        // (~11 MB/frame at 720p) — BUG-29. Only the buffer's contents change.
        let mut img = RgbImage { w: 0, h: 0, px: Vec::new() };
        let mut last_preview = std::time::Instant::now() - std::time::Duration::from_secs(1);
        let (mut ok, mut miss, mut decode_fail, mut n) = (0u64, 0u64, 0u64, 0usize);

        // Outer loop: (re)open the camera, so an unplug/replug recovers instead of freezing (BUG-30).
        while running.load(Ordering::Relaxed) {
            let mut cam = match open_camera(index, req_w, req_h, req_fps) {
                Ok(c) => c,
                Err(e) => {
                    pose.set_cam_error(format!("camera unavailable: {e}"));
                    eprintln!("VISION_ERROR camera open (index {index}): {e}");
                    // Back off ~2 s, then retry — unless we're shutting down.
                    for _ in 0..20 {
                        if !running.load(Ordering::Relaxed) {
                            return;
                        }
                        std::thread::sleep(std::time::Duration::from_millis(100));
                    }
                    continue;
                }
            };
            let res = cam.resolution();
            pose.set_cam_res(res.width(), res.height());
            pose.clear_cam_error();
            println!("POSE camera index={index} {}x{}", res.width(), res.height());

            let mut consec_frame_err = 0u32;
            while running.load(Ordering::Relaxed) {
                let frame = match cam.frame() {
                    Ok(f) => {
                        consec_frame_err = 0;
                        f
                    }
                    Err(e) => {
                        consec_frame_err += 1;
                        eprintln!("VISION_ERROR frame ({consec_frame_err}): {e}");
                        if consec_frame_err >= 10 {
                            pose.set_cam_error(format!("camera lost: {e}"));
                            break; // drop to the outer loop and try to reopen
                        }
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
                    Err(e) => {
                        decode_fail += 1; // count + log instead of silently dropping (BUG-32)
                        if decode_fail.is_multiple_of(30) {
                            eprintln!("VISION decode_fail={decode_fail} (last: {e})");
                        }
                        continue;
                    }
                };
                if let Some(cp) = &cockpit {
                    if last_preview.elapsed() >= std::time::Duration::from_millis(80) {
                        if let Some(jpeg) = encode_jpeg(decoded.as_raw(), decoded.width(), decoded.height()) {
                            cp.set_frame(jpeg);
                        }
                        last_preview = std::time::Instant::now();
                    }
                }
                img.w = decoded.width() as usize;
                img.h = decoded.height() as usize;
                img.px.clear();
                img.px.extend(decoded.as_raw().iter().map(|&b| b as f32));
                match pipe.estimate(&img) {
                    Ok(Some(est)) => {
                        ok += 1;
                        pose.update_raw(est.pose.0);
                    }
                    _ => miss += 1,
                }
                if (ok + miss).is_multiple_of(30) {
                    eprintln!("VISION ok={ok} miss={miss} decode_fail={decode_fail} yaw={:.1}", *pose.yaw.lock().unwrap());
                }
            }
            let _ = cam.stop_stream(); // release the device before reopen / exit (BUG-30)
        }
    });
}

/// Open a camera at a requested resolution/fps (0 ⇒ a controlled 640×480@30 default, not nokhwa's
/// `AbsoluteHighestFrameRate` which can pick a tiny or 1080p mode — BUG-28).
fn open_camera(index: u32, w: u32, h: u32, fps: u32) -> Result<nokhwa::Camera, String> {
    use nokhwa::pixel_format::RgbFormat;
    use nokhwa::utils::{
        CameraFormat, CameraIndex, FrameFormat, RequestedFormat, RequestedFormatType, Resolution,
    };
    let (rw, rh) = if w > 0 && h > 0 { (w, h) } else { (640, 480) };
    let rfps = if fps > 0 { fps } else { 30 };
    let want = CameraFormat::new(Resolution::new(rw, rh), FrameFormat::MJPEG, rfps);
    let fmt = RequestedFormat::new::<RgbFormat>(RequestedFormatType::Closest(want));
    let mut cam = nokhwa::Camera::new(CameraIndex::Index(index), fmt).map_err(|e| e.to_string())?;
    cam.open_stream().map_err(|e| e.to_string())?;
    Ok(cam)
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
                pose.set_cam_error(format!("pipeline: {e}"));
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
                pose.set_cam_error(format!("pipeline: {e}"));
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
