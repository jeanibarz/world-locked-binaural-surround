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
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use vision_client::pipeline::{decode_image, Pipeline};

/// Shared, recenter-able head yaw (deg). The first valid pose defines neutral (0).
pub struct PoseState {
    pub yaw: Mutex<f64>,
    yaw0: Mutex<Option<f64>>,
}

impl PoseState {
    pub fn new() -> Arc<Self> {
        Arc::new(Self { yaw: Mutex::new(0.0), yaw0: Mutex::new(None) })
    }
    fn update_raw(&self, raw_yaw: f64) {
        let mut y0 = self.yaw0.lock().unwrap();
        if y0.is_none() {
            *y0 = Some(raw_yaw); // first pose = neutral
        }
        *self.yaw.lock().unwrap() = raw_yaw - y0.unwrap();
    }
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
