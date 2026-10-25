//! `wlbs_engine` — the standalone zero-Python world-lock binaural engine.
//!
//! One Rust binary wires the four merged crates into a live pipeline: `audio-io` loopback-captures a
//! 7.1 source endpoint, `render_core_lib` world-lock renders it to binaural on the shared native
//! convolution kernel (`binaural_conv`), `audio-io` renders the stereo result to an output endpoint
//! through the cushioned drift-FIFO, and `control-plane` serves the web cockpit with live telemetry.
//! No `python`/`py.exe`, no interpreter — that is the north star.
//!
//! Head pose drives the world-lock. This build takes yaw from a demo rotator or a held value; the
//! `vision-client` camera→pose wire is the next stage (keeps this milestone free of the camera/ONNX
//! cross-compile surface so the audio+render+cockpit core is proven first).

// On non-Windows the WASAPI `engine` module is cfg'd out, so the pack loader is unused there; it is
// exercised on the real (Windows) target. Geometry stays used by its unit tests everywhere.
#![cfg_attr(not(windows), allow(dead_code))]

mod assets;
mod geometry;
mod pack;
mod packs;
mod telemetry;
mod vision;

#[cfg(not(windows))]
fn main() {
    eprintln!("wlbs_engine needs WASAPI (Windows). Build for x86_64-pc-windows-msvc.");
}

#[cfg(windows)]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    engine::run()
}

#[cfg(windows)]
mod engine {
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::{Arc, Mutex};
    use std::time::{Duration, Instant};

    use audio_io::wasapi_backend::{init_com, set_pro_audio, WasapiLoopback, WasapiRender};
    use audio_io::{cushion_blocks, AudioOutput, AudioSource, CushionFifo};
    use binaural_conv::BusConvolverCore;
    use control_plane::settings::SettingsStore;
    use control_plane::Cockpit;
    use render_core_lib::{GridRender, Lfe};

    use crate::geometry::head_relative_azimuths;
    use crate::pack::BrirPack;
    use crate::telemetry::{self, LatencyHistogram, TeleInput};

    fn arg(a: &[String], k: &str) -> Option<String> {
        a.iter().position(|x| x == k).and_then(|i| a.get(i + 1)).cloned()
    }
    fn flag(a: &[String], k: &str) -> bool {
        a.iter().any(|x| x == k)
    }

    type StereoBlock = Vec<f32>; // interleaved L,R, length block*2

    /// The live, user-adjustable engine settings — the backing state for every cockpit control and
    /// the `/api/get_config` round-trip. Persisted atomically via [`SettingsStore`] so a choice
    /// survives a restart (BUG-08); the capture loop applies the live-safe ones each tick (BUG-01).
    #[derive(Clone, PartialEq)]
    struct Settings {
        method: String,
        hrir_name: String,
        backend: String,
        comp: String,
        block: usize,
        cushion_ms: f64,
        exclusive: bool,
        hp_eq: bool,
        hp_model: Option<String>,
        predict: bool,
        camera_idx: Option<u32>,
        cam_w: u32,
        cam_h: u32,
        cam_fps: u32,
        drawer_tab: String,
        win_w: u32,
        win_h: u32,
    }

    impl Settings {
        /// Overlay the persisted JSON (from [`SettingsStore::load`]) onto the CLI/computed defaults.
        /// Persisted UI choices win for the knobs the cockpit owns; delete the settings file (or use
        /// the settings dialog's Reset) to return to defaults.
        fn overlay(&mut self, v: &serde_json::Value) {
            let o = match v.as_object() {
                Some(o) => o,
                None => return,
            };
            if let Some(s) = o.get("method").and_then(|x| x.as_str()) { self.method = s.into(); }
            if let Some(s) = o.get("hrir_name").and_then(|x| x.as_str()) { self.hrir_name = s.into(); }
            if let Some(s) = o.get("backend").and_then(|x| x.as_str()) { self.backend = s.into(); }
            if let Some(s) = o.get("comp").and_then(|x| x.as_str()) { self.comp = s.into(); }
            if let Some(n) = o.get("block").and_then(|x| x.as_u64()) { self.block = (n as usize).clamp(64, 8192); }
            if let Some(n) = o.get("cushion_ms").and_then(|x| x.as_f64()) { self.cushion_ms = n.clamp(1.0, 500.0); }
            if let Some(b) = o.get("exclusive").and_then(|x| x.as_bool()) { self.exclusive = b; }
            if let Some(b) = o.get("hp_eq").and_then(|x| x.as_bool()) { self.hp_eq = b; }
            if let Some(s) = o.get("hp_model").and_then(|x| x.as_str()) { self.hp_model = Some(s.into()); }
            if let Some(b) = o.get("predict").and_then(|x| x.as_bool()) { self.predict = b; }
            if let Some(n) = o.get("camera_idx").and_then(|x| x.as_u64()) { self.camera_idx = Some(n as u32); }
            if let Some(n) = o.get("cam_w").and_then(|x| x.as_u64()) { self.cam_w = n as u32; }
            if let Some(n) = o.get("cam_h").and_then(|x| x.as_u64()) { self.cam_h = n as u32; }
            if let Some(n) = o.get("cam_fps").and_then(|x| x.as_u64()) { self.cam_fps = n as u32; }
            if let Some(s) = o.get("drawer_tab").and_then(|x| x.as_str()) { self.drawer_tab = s.into(); }
            if let Some(n) = o.get("win_w").and_then(|x| x.as_u64()) { self.win_w = n as u32; }
            if let Some(n) = o.get("win_h").and_then(|x| x.as_u64()) { self.win_h = n as u32; }
        }

        /// Serialise for [`SettingsStore::save`] (the same keys [`Settings::overlay`] reads).
        fn to_json(&self) -> serde_json::Value {
            serde_json::json!({
                "method": self.method, "hrir_name": self.hrir_name, "backend": self.backend,
                "comp": self.comp, "block": self.block, "cushion_ms": self.cushion_ms,
                "exclusive": self.exclusive, "hp_eq": self.hp_eq, "hp_model": self.hp_model,
                "predict": self.predict, "camera_idx": self.camera_idx,
                "cam_w": self.cam_w, "cam_h": self.cam_h, "cam_fps": self.cam_fps,
                "drawer_tab": self.drawer_tab, "win_w": self.win_w, "win_h": self.win_h,
            })
        }
    }

    /// Build the full `/api/get_config` payload the cockpit + settings dialog read — the dropdown
    /// option lists plus every current value, so the UI reflects live state instead of stubs (BUG-17).
    fn build_config(
        s: &Settings,
        cameras_json: &serde_json::Value,
        hrir_list: &[String],
        samplerate: u32,
    ) -> serde_json::Value {
        serde_json::json!({
            "cameras": cameras_json,
            "hrir": if hrir_list.is_empty() { vec!["default".to_string()] } else { hrir_list.to_vec() },
            "methods": ["nearest"], // the engine renders nearest-ring only; the rest aren't implemented
            "resolutions": ["default", "320×240", "640×480", "848×480", "1280×720"],
            "framerates": ["default", "30 fps", "60 fps"],
            "samplerate": samplerate,
            "backend": s.backend, "method": s.method, "hrir_name": s.hrir_name, "comp": s.comp,
            "block": s.block, "cushion_ms": s.cushion_ms, "exclusive": s.exclusive,
            "hp_eq": s.hp_eq, "hp_models": s.hp_model.clone().map(|m| vec![m]),
            "predict": s.predict,
            "win_w": s.win_w, "win_h": s.win_h,
        })
    }

    pub fn run() -> Result<(), Box<dyn std::error::Error>> {
        use crate::{assets, packs};
        let a: Vec<String> = std::env::args().collect();

        // Pack management sub-commands (install-relative + %APPDATA%\WLBS\packs), then exit.
        if flag(&a, "--list-packs") {
            println!("packs dir: {}", packs::user_packs_dir().display());
            for p in packs::list_packs() {
                println!("  {}  {}", p.name, p.dir.display());
            }
            return Ok(());
        }
        if let Some(src) = arg(&a, "--import-pack") {
            let dst = packs::import_pack(std::path::Path::new(&src), arg(&a, "--name").as_deref())?;
            println!("imported -> {}", dst.display());
            return Ok(());
        }
        // List WASAPI render endpoints with channel counts, then exit — pick an 8-channel (7.1)
        // endpoint for --source and your headphone endpoint for --output (name substring, case-insensitive).
        if flag(&a, "--list-devices") {
            audio_io::wasapi_backend::init_com().map_err(|e| format!("init_com: {e}"))?;
            println!("render endpoints (name | channels | sample-rate):");
            for (name, ch, sr) in audio_io::wasapi_backend::list_render_endpoints_detailed()? {
                let tag = if ch >= 8 { "  <- 7.1-capable (use for --source)" } else { "" };
                println!("  {ch}ch  {sr:>6} Hz  {name}{tag}");
            }
            return Ok(());
        }

        // BRIR pack: --pack <name> (bundled default / %APPDATA%), else --brir-dir, else the bundled
        // default beside the exe. No hardcoded paths — a clean-machine install resolves itself.
        let brir_dir: PathBuf = if let Some(name) = arg(&a, "--pack") {
            packs::resolve_pack(&name).ok_or_else(|| format!("no pack named {name:?}"))?
        } else if let Some(d) = arg(&a, "--brir-dir") {
            PathBuf::from(d)
        } else {
            packs::bundled_default()
        };
        let source = arg(&a, "--source").unwrap_or_else(|| "VoiceMeeter".into());
        let output = arg(&a, "--output").unwrap_or_else(|| "Bigscreen".into());
        let port: u16 = arg(&a, "--port").and_then(|s| s.parse().ok()).unwrap_or(8732);
        let block: usize = arg(&a, "--block").and_then(|s| s.parse().ok()).unwrap_or(256);
        let latency_ms: f64 = arg(&a, "--latency-ms").and_then(|s| s.parse().ok()).unwrap_or(20.0);
        let yaw_sign: f64 = arg(&a, "--yaw-sign").and_then(|s| s.parse().ok()).unwrap_or(1.0);
        let rotate_demo = flag(&a, "--rotate-demo");
        // Install-relative asset resolution (no hardcoded paths): CLI → env → <exe>/… → %APPDATA%.
        let web_dir =
            assets::resolve_dir(arg(&a, "--web-dir").as_deref(), "WLBS_WEB_DIR", "web_cockpit")
                .to_string_lossy()
                .into_owned();
        let run_secs: f64 = arg(&a, "--secs").and_then(|s| s.parse().ok()).unwrap_or(0.0); // 0 = forever
        // Head-pose source (zero-Python vision-client). --camera-mjpeg pulls live frames from an
        // MJPEG URL; --face-image loops one image; else the demo rotator / held yaw.
        let onnx_dir = assets::resolve_dir(arg(&a, "--onnx-dir").as_deref(), "WLBS_ONNX_DIR", "assets")
            .to_string_lossy()
            .into_owned();
        let canonical = assets::resolve_file(
            arg(&a, "--canonical").as_deref(),
            "WLBS_CANONICAL",
            "assets/canonical_face_model.obj",
        )
        .to_string_lossy()
        .into_owned();
        let camera_mjpeg = arg(&a, "--camera-mjpeg");
        let face_image = arg(&a, "--face-image");
        let pose_stride: usize = arg(&a, "--pose-stride").and_then(|s| s.parse().ok()).unwrap_or(2);
        let camera_idx = arg(&a, "--camera").and_then(|s| s.parse::<u32>().ok());
        // Enumerate local cameras once, for the cockpit picker and the auto-default source.
        let cameras = crate::vision::enumerate_cameras();
        for (i, n) in &cameras {
            println!("CAMERA {i} {n}");
        }
        let cameras_json =
            serde_json::Value::Array(cameras.iter().map(|(i, n)| serde_json::json!([n, i])).collect());

        let pack = Arc::new(BrirPack::load(&brir_dir)?);
        println!("PACK sr={} taps={} ring={} lfe_idx={}", pack.samplerate, pack.n_taps, pack.n_ring, pack.lfe_idx);

        // --- live settings: CLI/computed defaults, then the persisted overlay (BUG-08) ---
        let store = SettingsStore::new(assets::appdata_wlbs().join("settings.json"));
        let defaults = Settings {
            method: "nearest".into(),
            hrir_name: arg(&a, "--pack").unwrap_or_else(|| "default".into()),
            backend: "rust".into(),
            comp: "off".into(),
            block,
            cushion_ms: latency_ms,
            exclusive: false,
            hp_eq: false,
            hp_model: None,
            predict: false,
            camera_idx,
            cam_w: 0,
            cam_h: 0,
            cam_fps: 0,
            drawer_tab: "transport".into(),
            win_w: 1320,
            win_h: 864,
        };
        let mut settings = defaults.clone();
        settings.overlay(&store.load());
        // An explicitly-passed CLI flag beats a persisted value (least surprise for a debug launch):
        // re-apply the ones the user typed after the persisted overlay.
        if arg(&a, "--block").is_some() { settings.block = block; }
        if arg(&a, "--latency-ms").is_some() { settings.cushion_ms = latency_ms; }
        if camera_idx.is_some() { settings.camera_idx = camera_idx; }
        let hrir_list: Vec<String> = packs::list_packs().into_iter().map(|p| p.name).collect();
        let mut block = settings.block; // the live block size (the settings dialog can change it)

        // Persist off the real-time capture thread: an `fsync` can stall tens of ms, which on the
        // capture thread would be an audio dropout. The thread sends settings JSON to this saver.
        let (persist_tx, persist_rx) = std::sync::mpsc::channel::<serde_json::Value>();
        std::thread::spawn(move || {
            while let Ok(v) = persist_rx.recv() {
                let _ = store.save(&v);
            }
        });

        // Shared state.
        let pose = crate::vision::PoseState::new();
        let fifo: Arc<Mutex<CushionFifo<StereoBlock>>> = {
            let cushion = cushion_blocks(settings.cushion_ms, pack.samplerate, block);
            Arc::new(Mutex::new(CushionFifo::new(cushion, cushion * 2, vec![0.0f32; block * 2])))
        };
        let running = Arc::new(AtomicBool::new(true));
        let out_status: Arc<Mutex<OutputStatus>> = Arc::new(Mutex::new(OutputStatus::default()));

        // --- control-plane cockpit (tokio) ---
        let cockpit = Cockpit::new(web_dir, build_config(&settings, &cameras_json, &hrir_list, pack.samplerate));
        {
            let cockpit = Arc::clone(&cockpit);
            std::thread::spawn(move || control_plane::serve_blocking(cockpit, port));
        }

        // --- head-pose source --- priority: explicit --camera > explicit mjpeg/image/demo >
        // persisted camera > first local camera (the product default) > held. A persisted camera must
        // NOT override an explicit `--rotate-demo`/`--face-image`/`--camera-mjpeg` (WSL + test flows).
        let auto_cam = if let Some(c) = camera_idx {
            Some(c)
        } else if camera_mjpeg.is_some() || face_image.is_some() || rotate_demo {
            None
        } else {
            settings.camera_idx.or_else(|| cameras.first().map(|(i, _)| *i))
        };
        let pose_source: &str;
        if let Some(idx) = auto_cam {
            pose_source = "camera";
            crate::vision::spawn_camera_pose(
                idx, onnx_dir, canonical, pose_stride, Arc::clone(&pose), Arc::clone(&running),
                Some(Arc::clone(&cockpit)),
            );
        } else if let Some(url) = camera_mjpeg {
            pose_source = "mjpeg";
            println!("POSE mjpeg {url}");
            crate::vision::spawn_mjpeg_pose(
                url, onnx_dir, canonical, pose_stride, Arc::clone(&pose), Arc::clone(&running),
            );
        } else if let Some(img) = face_image {
            pose_source = "image";
            println!("POSE image {img}");
            crate::vision::spawn_image_pose(img, onnx_dir, canonical, Arc::clone(&pose), Arc::clone(&running));
        } else if rotate_demo {
            pose_source = "demo";
            println!("POSE demo-rotator");
            let pose = Arc::clone(&pose);
            let running = Arc::clone(&running);
            std::thread::spawn(move || {
                let t0 = Instant::now();
                while running.load(Ordering::Relaxed) {
                    *pose.yaw.lock().unwrap() = 40.0 * (t0.elapsed().as_secs_f64() * 0.2).sin();
                    std::thread::sleep(Duration::from_millis(20));
                }
            });
        } else {
            pose_source = "held";
            println!("POSE none (held yaw 0)");
        }

        // --- output thread: drain the FIFO to the render endpoint ---
        {
            let fifo = Arc::clone(&fifo);
            let running = Arc::clone(&running);
            let output = output.clone();
            let out_status = Arc::clone(&out_status);
            std::thread::spawn(move || {
                if let Err(e) = output_loop(&output, fifo, running, block, &out_status) {
                    eprintln!("OUTPUT_ERROR {e}");
                    out_status.lock().unwrap().err = Some(e);
                }
            });
        }

        // --- capture + render thread (this thread) ---
        init_com()?;
        let mmcss = set_pro_audio();
        println!("MMCSS_PRO_AUDIO {mmcss}");
        let mut src = WasapiLoopback::open(&source)?;
        let sfmt = src.format();
        println!("SOURCE {:?} sr={} ch={}", source, sfmt.sample_rate, sfmt.channels);
        let n_in = sfmt.channels as usize;

        let mut render = build_render(&pack, n_in, block, latency_ms)?;

        let start = Instant::now();
        let mut in_acc: Vec<f32> = Vec::with_capacity(block * n_in * 4);
        let mut buf: Vec<f32> = Vec::with_capacity(block * n_in * 4);
        let mut last_tele = Instant::now();
        let mut last_audio = Instant::now();
        let mut rendered_blocks = 0u64;
        let mut render_ema = 0.0f64; // EMA of per-block render time as % of the block budget
        let mut render_peak = 0.0f64;
        let mut lat = LatencyHistogram::new(120, 20, 0.0, 120.0);
        let mut snapshot: Option<Settings> = None; // live edit-session snapshot (begin/commit/cancel)
        while running.load(Ordering::Relaxed) {
            if run_secs > 0.0 && start.elapsed().as_secs_f64() >= run_secs {
                break;
            }

            // --- drain + apply cockpit controls (BUG-01) ---
            let block_before = settings.block;
            let cush_before = settings.cushion_ms;
            let mut dirty = false;
            for (name, args) in cockpit.take_dispatched() {
                let a0 = args.get(0);
                match name.as_str() {
                    "set_predict" => { if let Some(b) = a0.and_then(|v| v.as_bool()) { settings.predict = b; dirty = true; } }
                    "recenter" => pose.recenter(),
                    "set_drawer_tab" => { if let Some(s) = a0.and_then(|v| v.as_str()) { settings.drawer_tab = s.into(); dirty = true; } }
                    "window_size" => {
                        if let (Some(w), Some(h)) = (a0.and_then(|v| v.as_u64()), args.get(1).and_then(|v| v.as_u64())) {
                            settings.win_w = w as u32; settings.win_h = h as u32; dirty = true;
                        }
                    }
                    "set_method" => { if let Some(s) = a0.and_then(|v| v.as_str()) { settings.method = s.into(); dirty = true; } }
                    "set_backend" => { if let Some(s) = a0.and_then(|v| v.as_str()) { settings.backend = s.into(); dirty = true; } }
                    "set_hrir" => { if let Some(s) = a0.and_then(|v| v.as_str()) { settings.hrir_name = s.into(); dirty = true; } }
                    "set_comp" => { if let Some(s) = a0.and_then(|v| v.as_str()) { settings.comp = s.into(); dirty = true; } }
                    "set_exclusive" => { if let Some(b) = a0.and_then(|v| v.as_bool()) { settings.exclusive = b; dirty = true; } }
                    "set_hpeq" => { if let Some(b) = a0.and_then(|v| v.as_bool()) { settings.hp_eq = b; dirty = true; } }
                    "set_hp_model" => { if let Some(s) = a0.and_then(|v| v.as_str()) { settings.hp_model = Some(s.into()); dirty = true; } }
                    "set_block" => { if let Some(n) = a0.and_then(|v| v.as_u64()) { settings.block = (n as usize).clamp(64, 8192); dirty = true; } }
                    "set_cushion" => { if let Some(n) = a0.and_then(|v| v.as_f64()) { settings.cushion_ms = n.clamp(1.0, 500.0); dirty = true; } }
                    "set_camera" => { if let Some(n) = a0.and_then(|v| v.as_u64()) { settings.camera_idx = Some(n as u32); dirty = true; } }
                    "set_cam_format" => {
                        if let (Some(w), Some(h)) = (a0.and_then(|v| v.as_u64()), args.get(1).and_then(|v| v.as_u64())) {
                            settings.cam_w = w as u32; settings.cam_h = h as u32;
                            settings.cam_fps = args.get(2).and_then(|v| v.as_u64()).unwrap_or(0) as u32;
                            dirty = true;
                        }
                    }
                    "settings_begin" => snapshot = Some(settings.clone()),
                    "settings_commit" => { let _ = persist_tx.send(settings.to_json()); snapshot = None; }
                    "settings_cancel" => {
                        if let Some(prev) = snapshot.take() {
                            if prev != settings { settings = prev; dirty = true; }
                        }
                    }
                    // Reset previews live and persists through the same session gate below (Confirm
                    // commits it; a mid-dialog crash does not leave defaults saved).
                    "reset_defaults" => { settings = defaults.clone(); dirty = true; }
                    _ => {} // blind_* A/B-test + any unknown control: no engine effect yet
                }
            }
            if dirty {
                cockpit.set_config(build_config(&settings, &cameras_json, &hrir_list, pack.samplerate));
                // Persist immediately for the main-cockpit controls (predict/recenter/window/tab);
                // settings-dialog changes are held until Confirm (while a snapshot is active).
                if snapshot.is_none() {
                    let _ = persist_tx.send(settings.to_json());
                }
                // Block/cushion are the knobs we can safely re-apply to the live audio graph here on
                // the capture thread (method/hrir/exclusive/camera need a restart — persisted above).
                // Only a block change rebuilds the convolver (`render`); a cushion-only change rebuilds
                // just the FIFO, so changing the cushion doesn't reset the convolution state.
                let block_changed = settings.block != block_before;
                if block_changed || settings.cushion_ms != cush_before {
                    let new_block = settings.block;
                    let cushion = cushion_blocks(settings.cushion_ms, pack.samplerate, new_block);
                    let render_ok = if block_changed {
                        match build_render(&pack, n_in, new_block, latency_ms) {
                            Ok(r) => { render = r; true }
                            Err(e) => { eprintln!("RECONFIG_ERROR {e}"); settings.block = block_before; false }
                        }
                    } else {
                        true
                    };
                    if render_ok {
                        block = settings.block; // only advance the live block once render matches it
                        *fifo.lock().unwrap() =
                            CushionFifo::new(cushion, cushion * 2, vec![0.0f32; block * 2]);
                        in_acc.clear();
                        println!("RECONFIG block={block} cushion={cushion}");
                    }
                }
            }

            buf.clear();
            // A capture timeout no longer skips telemetry: we fall through to the heartbeat below so
            // an idle source keeps the cockpit live (BUG-05). A short timeout keeps the control drain
            // and heartbeat responsive (~20 Hz) even when an idle loopback emits no capture events.
            if src.read(&mut buf, 50)?.is_some() {
                last_audio = Instant::now();
                in_acc.extend_from_slice(&buf);
                // Sample the head yaw now, after the blocking read, so the azimuths the block renders
                // with are fresh (not up to a device period / timeout stale).
                let az = head_relative_azimuths(*pose.yaw.lock().unwrap(), yaw_sign);
                let block_time = block as f64 / pack.samplerate.max(1) as f64;
                while in_acc.len() >= block * n_in {
                    let pcm: Vec<f32> = in_acc.drain(..block * n_in).collect();
                    let pcm64: Vec<f64> = pcm.iter().map(|&v| v as f64).collect();
                    let mut out = vec![0.0f64; block * 2];
                    let t0 = Instant::now();
                    render.process_into(&pcm64, &az[..n_in.min(8)], &mut out);
                    let pct = if block_time > 0.0 { t0.elapsed().as_secs_f64() / block_time * 100.0 } else { 0.0 };
                    render_ema = render_ema * 0.9 + pct * 0.1;
                    if pct > render_peak { render_peak = pct; }
                    let stereo: StereoBlock = out.iter().map(|&v| v as f32).collect();
                    fifo.lock().unwrap().push(stereo);
                    rendered_blocks += 1;
                }
            }

            // --- telemetry heartbeat (time-driven, runs even when the source is idle) ---
            if last_tele.elapsed() >= Duration::from_millis(50) {
                let cur_yaw = *pose.yaw.lock().unwrap();
                let source_idle = last_audio.elapsed() >= Duration::from_millis(250);
                render_peak = (render_peak * 0.95).max(render_ema); // slow decay toward the mean
                let (cushion_now, fill_now, st) = {
                    let f = fifo.lock().unwrap();
                    (f.cushion(), f.len(), f.stats())
                };
                lat.push(telemetry::estimate_total_ms(block, pack.samplerate, cushion_now, render_ema));
                let os = out_status.lock().unwrap().clone();
                let pose_stale = pose.millis_since_update().map_or(false, |m| m >= 1500);
                let state = telemetry::build_state(&TeleInput {
                    title: "WLBS Engine",
                    yaw_deg: cur_yaw,
                    predict: settings.predict,
                    block,
                    sample_rate: pack.samplerate,
                    cushion_blocks: cushion_now,
                    fifo_fill: fill_now,
                    fifo_capacity: cushion_now * 2,
                    underruns: st.underruns,
                    overflow_drops: st.overflow_drops,
                    rendered_blocks,
                    render_load_pct: render_ema,
                    render_peak_pct: render_peak,
                    source_idle,
                    source_name: &source,
                    source_sr: sfmt.sample_rate,
                    source_ch: sfmt.channels,
                    output_name: &output,
                    output_sr: os.sr,
                    output_err: os.err.as_deref(),
                    pose_source,
                    tracking: pose.is_tracking(),
                    pose_stale,
                    cam_res: pose.cam_res(),
                    mmcss,
                    lat: &lat,
                });
                cockpit.push_state(telemetry::wrap_state(state));
                last_tele = Instant::now();
            }
        }
        running.store(false, Ordering::Relaxed);
        let _ = src.stop();
        println!("DONE rendered_blocks={rendered_blocks} elapsed_s={:.2}", start.elapsed().as_secs_f64());
        Ok(())
    }

    /// Output-thread status shared with the capture thread for cockpit health telemetry: the opened
    /// endpoint's format and any fatal error. (BUG-04's full fix — flipping `running` on output
    /// death — lands in the audio-robustness batch; this is the telemetry surface it needs.)
    #[derive(Default, Clone)]
    struct OutputStatus {
        sr: u32,
        #[allow(dead_code)]
        ch: u16,
        err: Option<String>,
    }

    /// Build the world-lock renderer from the pack, adapting to the source channel count (8 = full
    /// 7.1 ring + LFE; 2 = front L/R only, for a stereo source).
    fn build_render(pack: &BrirPack, n_in: usize, block: usize, latency_ms: f64) -> Result<GridRender, String> {
        let fade = (latency_ms * 1e-3 * pack.samplerate as f64).round().max(1.0) as i64;
        match n_in {
            8 => {
                let lfe = Lfe::Brir {
                    conv: BusConvolverCore::new(&pack.lfe_left, &pack.lfe_right, 1, pack.n_taps, block)?,
                    idx: pack.lfe_idx,
                };
                GridRender::new(
                    &pack.ring_left, &pack.ring_right, pack.n_ring, pack.n_taps,
                    pack.ring_az.clone(), pack.dir_idx.clone(), 8, fade, block, lfe,
                )
            }
            2 => {
                // front L,R are ring grid buses 0,1 (az +30/-30); route the two input columns to them.
                let taps = pack.n_taps;
                let left = [&pack.ring_left[0..taps], &pack.ring_left[taps..2 * taps]].concat();
                let right = [&pack.ring_right[0..taps], &pack.ring_right[taps..2 * taps]].concat();
                GridRender::new(&left, &right, 2, taps, vec![pack.ring_az[0], pack.ring_az[1]],
                    vec![0, 1], 2, fade, block, Lfe::None)
            }
            other => Err(format!("source has {other} channels; engine handles 8 (7.1) or 2 (stereo)")),
        }
    }

    /// Pull stereo blocks from the FIFO and write them to the output device each event.
    fn output_loop(
        output_name: &str,
        fifo: Arc<Mutex<CushionFifo<StereoBlock>>>,
        running: Arc<AtomicBool>,
        block: usize,
        status: &Arc<Mutex<OutputStatus>>,
    ) -> Result<(), String> {
        init_com()?;
        set_pro_audio();
        let mut out = WasapiRender::open(output_name)?;
        let ofmt = out.format();
        println!("OUTPUT {:?} sr={} ch={}", output_name, ofmt.sample_rate, ofmt.channels);
        {
            let mut s = status.lock().unwrap();
            s.sr = ofmt.sample_rate;
            s.ch = ofmt.channels;
        }
        if ofmt.channels != 2 {
            return Err(format!("output endpoint is {}ch; need a stereo endpoint", ofmt.channels));
        }
        let mut pending: Vec<f32> = Vec::with_capacity(block * 4);
        while running.load(Ordering::Relaxed) {
            if !out.wait(1000)? {
                continue;
            }
            let need = out.frames_needed()?;
            while pending.len() / 2 < need {
                let (_p, blk) = fifo.lock().unwrap().pull();
                pending.extend_from_slice(&blk.unwrap_or_else(|| vec![0.0f32; block * 2]));
            }
            let take = need * 2;
            out.write(&pending[..take])?;
            pending.drain(..take);
        }
        let _ = out.stop();
        Ok(())
    }
}
