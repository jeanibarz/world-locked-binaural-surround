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

use crate::geometry::{pack_landmarks, LandmarkFrame};

fn now_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis() as u64).unwrap_or(0)
}

/// Shared, recenter-able head yaw (deg). The first valid pose defines neutral (0). Also carries
/// tracker liveness (last-update timestamp) and the camera resolution, for cockpit telemetry.
pub struct PoseState {
    pub yaw: Mutex<f64>,
    /// Neutral reference, stored in the solver's **raw** convention (sign not yet applied), so the
    /// tracking-direction sign can flip live (see `invert`) by simply mirroring the output around
    /// this neutral — no re-centre, no discontinuity.
    yaw0: Mutex<Option<f64>>,
    /// Head-tracking direction override. The published `yaw` is `sign * (raw - neutral)` with
    /// `sign = +1` by default (the raw offset, the solver's own handedness). The tracking direction
    /// is corrected upstream by un-mirroring the camera frame (see `flip_h`, BUG #191); `invert =
    /// true` is an advanced override that sets `sign = -1`, for a mount still reversed after the
    /// flip. This sign is the single source of truth for *both* the radar (rotates by `-yaw`) and
    /// the audio azimuths (`speaker_az - yaw`), so they can never disagree in direction.
    invert: AtomicBool,
    /// Un-mirror the camera frame horizontally before the pose pipeline (BUG #191). A typical webcam
    /// delivers a selfie-mirrored image, which reverses the detected yaw *and* the landmark/ear
    /// handedness; flipping it once at the source corrects all of them together, and the cockpit
    /// preview then matches what the solver sees. On by default; the live cockpit toggle and
    /// `--flip-h` set it. Only the direct-camera source honors it (MJPEG/image sources are passive).
    flip_h: AtomicBool,
    /// Un-mirror the camera frame vertically (an upside-down mount). Off by default; `--flip-v` /
    /// the cockpit toggle set it. Same source-side application as `flip_h`.
    flip_v: AtomicBool,
    /// Epoch-millis of the last pose update, or 0 if no pose has been produced yet.
    last_update: AtomicU64,
    /// The active camera resolution, once a camera stream is open.
    cam_res: Mutex<Option<(u32, u32)>>,
    /// A camera/pipeline open failure, so the cockpit can say "camera unavailable" instead of
    /// showing a permanently-searching tracker (BUG-10).
    cam_error: Mutex<Option<String>>,
    /// The latest FaceMesh overlay for the cockpit preview (#185), or `None` when the last pipeline
    /// run found no face. Written by the camera thread, read at the telemetry cadence. The overlay
    /// is purely cosmetic — it never feeds the world-lock — so a missed update just leaves the last
    /// frame until the next telemetry tick re-reads it.
    landmarks: Mutex<Option<LandmarkFrame>>,
    /// The camera the capture thread should run: `(id, w, h, fps)` (0 = the controlled default,
    /// see `open_camera`). Settings changes write this; the running thread reopens the new
    /// device/format without an engine restart (BUG-34 live camera switch).
    cam_req: Mutex<(String, u32, u32, u32)>,
    /// Bumps on every real change to `cam_req`. The capture loop polls it per frame and reopens
    /// when it differs from the generation it opened with.
    cam_req_gen: AtomicU64,
}

impl PoseState {
    pub fn new() -> Arc<Self> {
        Arc::new(Self {
            yaw: Mutex::new(0.0),
            yaw0: Mutex::new(None),
            invert: AtomicBool::new(false),
            flip_h: AtomicBool::new(false),
            flip_v: AtomicBool::new(false),
            last_update: AtomicU64::new(0),
            cam_res: Mutex::new(None),
            cam_error: Mutex::new(None),
            landmarks: Mutex::new(None),
            cam_req: Mutex::new((String::new(), 0, 0, 0)),
            cam_req_gen: AtomicU64::new(0),
        })
    }

    /// Set the camera the capture thread should run — its `id` plus resolution/fps (0 = the
    /// controlled default). Bumps the generation only on a real change, so the running camera
    /// thread reopens the new device/format without an engine restart (BUG-34), and a no-op
    /// request (e.g. an unrelated settings change re-applying the same camera) is ignored.
    pub fn set_cam_request(&self, id: String, w: u32, h: u32, fps: u32) {
        let mut r = self.cam_req.lock().unwrap();
        let next = (id, w, h, fps);
        if *r != next {
            *r = next;
            self.cam_req_gen.fetch_add(1, Ordering::Relaxed);
        }
    }

    /// The current camera request `(id, w, h, fps)` paired with the generation it belongs to —
    /// read once per (re)open so the capture loop knows which generation it is serving. The tuple
    /// and the generation are read **under the same held lock** (the explicit `guard` scope), so
    /// they are a consistent snapshot: the loop can never pair a new generation with a stale tuple,
    /// nor miss a switch. Do not refactor this to drop the guard before the `load`.
    fn cam_request(&self) -> ((String, u32, u32, u32), u64) {
        let guard = self.cam_req.lock().unwrap();
        (guard.clone(), self.cam_req_gen.load(Ordering::Relaxed))
    }

    /// The current camera-request generation (cheap; the capture loop polls this per frame).
    pub fn cam_req_gen(&self) -> u64 {
        self.cam_req_gen.load(Ordering::Relaxed)
    }

    /// Store the latest FaceMesh overlay (`Some` = a face was found this frame, `None` = a miss).
    /// Called on the camera thread (#185).
    pub fn set_landmarks(&self, lm: Option<LandmarkFrame>) {
        *self.landmarks.lock().unwrap() = lm;
    }

    /// The latest overlay, cloned for the telemetry frame (#185). `None` when there's no current
    /// face. The per-mille packing keeps the clone small (≈2 KB for the 478-point mesh).
    pub fn landmarks(&self) -> Option<LandmarkFrame> {
        self.landmarks.lock().unwrap().clone()
    }

    /// Current tracking-direction state (`true` = inverted). Mirrored into cockpit telemetry so the
    /// "Invert head tracking" switch reflects the live value.
    pub fn inverted(&self) -> bool {
        self.invert.load(Ordering::Relaxed)
    }

    /// Flip the tracking direction live. Neutral is stored in raw units, so the published `yaw`
    /// simply mirrors around 0 — no jump, no re-centre. Applied on startup from the persisted
    /// setting and on the cockpit toggle (BUG #189).
    pub fn set_inverted(&self, invert: bool) {
        // Hold `yaw0` for the whole flip, the same lock order `update_raw`/`recenter` use (yaw0 then
        // yaw), so a concurrent pose can't land between the swap and the mirror and leave a
        // wrong-sign yaw for a frame.
        let _y0 = self.yaw0.lock().unwrap();
        let was = self.invert.swap(invert, Ordering::Relaxed);
        if was != invert {
            // Reflect the new sign in the already-published yaw immediately, so the radar/audio
            // flip on the same frame the toggle is pressed rather than on the next pose.
            let mut y = self.yaw.lock().unwrap();
            *y = -*y;
        }
    }

    /// Horizontal camera un-mirror state (`true` = flip applied). Mirrored into cockpit telemetry.
    pub fn flip_h(&self) -> bool {
        self.flip_h.load(Ordering::Relaxed)
    }
    /// Vertical camera flip state (`true` = flip applied). Mirrored into cockpit telemetry.
    pub fn flip_v(&self) -> bool {
        self.flip_v.load(Ordering::Relaxed)
    }
    /// Set the camera flips live (BUG #191). The camera thread reads each flag fresh at the next
    /// frame, so a cockpit toggle takes effect without a restart.
    ///
    /// When the orientation actually changes, the captured neutral is reset: a horizontal flip
    /// negates the solver's raw yaw, so the neutral captured in the old orientation would make the
    /// heading read wrong (non-zero at the real neutral) until the operator hit Recenter — which is
    /// exactly when they'd be testing the flip on the host. Clearing `yaw0` re-captures neutral from
    /// the next pose, zeroing the heading at the moment of the toggle. `yaw0` is held across the swap
    /// (same lock order as `update_raw`/`recenter`/`set_inverted`) so a concurrent pose can't capture
    /// a neutral in the orientation we're leaving.
    pub fn set_flips(&self, flip_h: bool, flip_v: bool) {
        let mut y0 = self.yaw0.lock().unwrap();
        let was_h = self.flip_h.swap(flip_h, Ordering::Relaxed);
        let was_v = self.flip_v.swap(flip_v, Ordering::Relaxed);
        if was_h != flip_h || was_v != flip_v {
            *y0 = None; // next pose defines the new neutral in the new orientation
            *self.yaw.lock().unwrap() = 0.0;
        }
    }

    fn update_raw(&self, raw_yaw: f64) {
        let mut y0 = self.yaw0.lock().unwrap();
        if y0.is_none() {
            *y0 = Some(raw_yaw); // first pose = neutral (stored raw, sign-independent)
        }
        *self.yaw.lock().unwrap() =
            crate::geometry::published_yaw(raw_yaw, y0.unwrap(), self.inverted());
        self.last_update.store(now_ms(), Ordering::Relaxed);
    }

    /// Fold the current offset into neutral so the direction the head faces right now reads as 0°
    /// (the cockpit Recenter control). A no-op before the first pose. Fixes BUG-09.
    pub fn recenter(&self) {
        let mut y0 = self.yaw0.lock().unwrap();
        let mut y = self.yaw.lock().unwrap();
        if let Some(cur0) = *y0 {
            *y0 = Some(crate::geometry::recenter_neutral(cur0, *y, self.inverted()));
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

/// Enumerate the local cameras (id, human name) for the cockpit picker. Empty on error / none. The
/// id is the nokhwa `CameraIndex` rendered as a string — the decimal index for an `Index` camera, or
/// the opaque string for a `String`-indexed one. Both kinds are now listed and selectable via that id
/// (BUG-34; string cameras used to be dropped).
///
/// The nokhwa query runs on a short-lived joined thread so MediaFoundation's `CoInitialize` happens
/// there, not on the engine's main thread — otherwise it would pin the main thread's COM apartment
/// before the engine's own `init_com()`/`initialize_mta()` runs, aborting startup with
/// `RPC_E_CHANGED_MODE` (BUG-35). The child thread dies right after, releasing its apartment.
pub fn enumerate_cameras() -> Vec<(String, String)> {
    std::thread::spawn(|| {
        use nokhwa::query;
        use nokhwa::utils::{ApiBackend, CameraIndex};
        match query(ApiBackend::Auto) {
            Ok(list) => list
                .iter()
                .map(|c| {
                    let id = match c.index() {
                        CameraIndex::Index(i) => i.to_string(),
                        CameraIndex::String(s) => s.clone(),
                    };
                    (id, c.human_name())
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

/// Parse a camera id string into a nokhwa `CameraIndex`: a decimal id is an `Index`, anything else a
/// `String` index. Mirrors how [`enumerate_cameras`] renders them.
fn camera_index_from_id(id: &str) -> nokhwa::utils::CameraIndex {
    use nokhwa::utils::CameraIndex;
    match id.parse::<u32>() {
        Ok(i) => CameraIndex::Index(i),
        Err(_) => CameraIndex::String(id.to_string()),
    }
}

/// Run the webcam pose source: open the camera named by `pose`'s current request (its id +
/// resolution/fps, 0 = a sane default), run the pose pipeline on ~every `stride`-th frame, update
/// `pose`, and feed the cockpit preview. Stride is clamped to ≥1 by the caller. Reopens the device
/// if it is unplugged, **or when the camera request changes** (a live camera/format switch from the
/// settings dialog — BUG-34), and releases it on exit. Seed the request with
/// [`PoseState::set_cam_request`] before spawning.
pub fn spawn_camera_pose(
    onnx_dir: String,
    canonical: String,
    stride: usize,
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
        // Reused scratch for the un-mirrored frame (BUG #191), so flipping doesn't allocate a
        // full-frame buffer every frame (BUG-29). Empty until a flip is first active.
        let mut flip_buf: Vec<u8> = Vec::new();
        let mut last_preview = std::time::Instant::now() - std::time::Duration::from_secs(1);
        let (mut ok, mut miss, mut decode_fail, mut n) = (0u64, 0u64, 0u64, 0usize);

        // Outer loop: (re)open the camera, so an unplug/replug recovers instead of freezing (BUG-30)
        // and a settings-dialog camera/format switch takes effect live (BUG-34). The request (id +
        // resolution/fps) is re-read here each time, so the newest selection wins on every reopen.
        while running.load(Ordering::Relaxed) {
            let ((id, req_w, req_h, req_fps), open_gen) = pose.cam_request();
            let mut cam = match open_camera(&id, req_w, req_h, req_fps) {
                Ok(c) => c,
                Err(e) => {
                    pose.set_cam_error(format!("camera unavailable: {e}"));
                    eprintln!("VISION_ERROR camera open (id {id}): {e}");
                    // Back off ~2 s, then retry — unless we're shutting down or the user picked a
                    // different camera meanwhile (then retry immediately with the new request).
                    for _ in 0..20 {
                        if !running.load(Ordering::Relaxed) {
                            return;
                        }
                        if pose.cam_req_gen() != open_gen {
                            break;
                        }
                        std::thread::sleep(std::time::Duration::from_millis(100));
                    }
                    continue;
                }
            };
            let res = cam.resolution();
            pose.set_cam_res(res.width(), res.height());
            pose.clear_cam_error();
            println!("POSE camera id={id} {}x{}", res.width(), res.height());

            let mut consec_frame_err = 0u32;
            while running.load(Ordering::Relaxed) {
                // A camera/format switch arrived → drop this device and reopen with the new request.
                if pose.cam_req_gen() != open_gen {
                    break;
                }
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
                // Un-mirror the camera at the source (BUG #191) BEFORE the preview and the pose
                // pipeline, so the detected yaw, the landmarks, the ear handedness and the cockpit
                // preview all share one orientation. With no flip active, the decoded bytes pass
                // straight through (no copy).
                let (w, h) = (decoded.width() as usize, decoded.height() as usize);
                let raw = decoded.as_raw();
                let (fh, fv) = (pose.flip_h(), pose.flip_v());
                let rgb: &[u8] = if (fh || fv) && raw.len() >= w * h * 3 {
                    flip_rgb_into(&mut flip_buf, raw, w, h, fh, fv);
                    &flip_buf
                } else {
                    raw
                };
                if let Some(cp) = &cockpit {
                    if last_preview.elapsed() >= std::time::Duration::from_millis(80) {
                        if let Some(jpeg) = encode_jpeg(rgb, w as u32, h as u32) {
                            cp.set_frame(jpeg);
                        }
                        last_preview = std::time::Instant::now();
                    }
                }
                img.w = w;
                img.h = h;
                img.px.clear();
                img.px.extend(rgb.iter().map(|&b| b as f32));
                match pipe.estimate(&img) {
                    Ok(Some(est)) => {
                        ok += 1;
                        pose.update_raw(est.pose.0);
                        // Capture the FaceMesh overlay for the cockpit preview (#185). The landmarks
                        // are already in the un-mirrored frame's normalized space (same frame as the
                        // `/camera` JPEG above), so the overlay aligns with the preview. Purely
                        // cosmetic; it never touches the audio render thread.
                        pose.set_landmarks(pack_landmarks(&est.landmarks));
                    }
                    _ => {
                        miss += 1;
                        pose.set_landmarks(None); // no face this frame → clear the overlay
                    }
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
/// `AbsoluteHighestFrameRate` which can pick a tiny or 1080p mode — BUG-28). The mode is chosen by
/// [`crate::camera_fmt::pick_camera_mode`] from the device's own capability list, so it opens on
/// cameras that offer no MJPEG (issue #171), not just those that happen to match a fixed request.
fn open_camera(id: &str, w: u32, h: u32, fps: u32) -> Result<nokhwa::Camera, String> {
    use nokhwa::pixel_format::RgbFormat;
    use nokhwa::utils::{FrameFormat, RequestedFormat, RequestedFormatType};
    let (rw, rh) = if w > 0 && h > 0 { (w, h) } else { (640, 480) };
    let rfps = if fps > 0 { fps } else { 30 };

    // Open permissively first. `None` fulfils with the first decodable mode, which succeeds
    // whenever the device exposes *any* color format. We deliberately do NOT ask nokhwa for a
    // `Closest(MJPEG …)` mode: its matcher only considers modes whose `FrameFormat` equals the one
    // requested (and its fps step demands an exact resolution), so a hard-coded MJPEG request fails
    // with "Could not initialize MediaFoundation: Failed to fulfill requested format" on the many
    // Windows cameras that expose only YUYV/NV12 (issue #171 regression).
    let probe = RequestedFormat::new::<RgbFormat>(RequestedFormatType::None);
    let mut cam = nokhwa::Camera::new(camera_index_from_id(id), probe).map_err(|e| e.to_string())?;

    // Then pick the mode closest to the requested resolution/fps from what the device *actually*
    // supports, and apply it before streaming — a controlled resolution (BUG-28 / issue #171), but
    // chosen robustly across whatever pixel formats the camera offers. If the device can't be
    // queried or nothing is returned, the probe mode stays selected and is what `open_stream` runs.
    let formats = cam.compatible_camera_formats().unwrap_or_default();
    let modes: Vec<(u32, u32, u32, bool)> = formats
        .iter()
        .map(|f| {
            let r = f.resolution();
            (r.x(), r.y(), f.frame_rate(), f.format() == FrameFormat::MJPEG)
        })
        .collect();
    if let Some(i) = crate::camera_fmt::pick_camera_mode(&modes, rw, rh, rfps) {
        // Best-effort: the probe mode is already selected, so a backend that refuses the switch
        // must not sink the whole open. `Exact(formats[i])` is guaranteed to fulfil (the mode came
        // straight from the device's own list), sidestepping nokhwa's brittle `Closest`.
        let want = RequestedFormat::new::<RgbFormat>(RequestedFormatType::Exact(formats[i]));
        let _ = cam.set_camera_requset(want);
    }
    cam.open_stream().map_err(|e| e.to_string())?;
    Ok(cam)
}

/// Write `src` (interleaved RGB8, `w`×`h`, row-major) into `dst` with a horizontal and/or vertical
/// flip (BUG #191 camera un-mirror). `dst` is resized in place and reused across frames. The caller
/// guarantees `src.len() >= w*h*3` and that at least one of `fh`/`fv` is set.
fn flip_rgb_into(dst: &mut Vec<u8>, src: &[u8], w: usize, h: usize, fh: bool, fv: bool) {
    let stride = w * 3;
    dst.resize(h * stride, 0);
    for y in 0..h {
        let sy = if fv { h - 1 - y } else { y };
        let srow = &src[sy * stride..sy * stride + stride];
        let drow = &mut dst[y * stride..y * stride + stride];
        if fh {
            for x in 0..w {
                let sx = w - 1 - x;
                drow[x * 3..x * 3 + 3].copy_from_slice(&srow[sx * 3..sx * 3 + 3]);
            }
        } else {
            drow.copy_from_slice(srow);
        }
    }
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
