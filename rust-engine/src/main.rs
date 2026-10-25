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
mod interp;
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

    use crate::geometry::{head_relative_azimuths, head_relative_azimuths_5p1};
    use crate::pack::BrirPack;
    use crate::telemetry::{self, LatencyHistogram, TeleInput};

    // NOTE: `arg` returns the first occurrence's following token (first-match precedence), and takes
    // the next token verbatim as the value — so a value-flag written with no value would swallow the
    // following flag. Pass each value flag once, with a value.
    fn arg(a: &[String], k: &str) -> Option<String> {
        a.iter().position(|x| x == k).and_then(|i| a.get(i + 1)).cloned()
    }
    fn flag(a: &[String], k: &str) -> bool {
        a.iter().any(|x| x == k)
    }
    /// Parse a numeric/typed value flag: `Ok(None)` if absent, `Ok(Some(v))` if present and valid,
    /// and an `Err` if present but unparseable — so a typo fails loudly instead of silently falling
    /// back to the default (BUG-22).
    fn arg_num<T: std::str::FromStr>(a: &[String], k: &str) -> Result<Option<T>, String> {
        match arg(a, k) {
            None => Ok(None),
            Some(v) => v.parse::<T>().map(Some).map_err(|_| format!("invalid value for {k}: {v:?}")),
        }
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
        /// Selected camera id (decimal index or opaque string, per nokhwa `CameraIndex`).
        camera: Option<String>,
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
            // New string `camera` id; fall back to the legacy numeric `camera_idx` for old settings.
            if let Some(s) = o.get("camera").and_then(|x| x.as_str()) {
                self.camera = Some(s.into());
            } else if let Some(n) = o.get("camera_idx").and_then(|x| x.as_u64()) {
                self.camera = Some(n.to_string());
            }
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
                "predict": self.predict, "camera": self.camera,
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
            "methods": ["nearest", "timealign", "minphase", "notchwarp"], // ring-interp methods (#187)
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
        let port: u16 = arg_num(&a, "--port")?.unwrap_or(8732);
        let block: usize = arg_num(&a, "--block")?.unwrap_or(256);
        let latency_ms: f64 = arg_num(&a, "--latency-ms")?.unwrap_or(20.0);
        let yaw_sign: f64 = arg_num(&a, "--yaw-sign")?.unwrap_or(1.0);
        // Ring-interpolation method (#187): nearest | timealign | minphase | notchwarp. The owner
        // prefers timealign, so it's the default. `--interp-step` sets the dense-ring spacing (deg):
        // finer = smoother rotation but more convolution buses (measured cost in the PR); 15° is a
        // headroom-safe default. `nearest` ignores the step (it snaps to the measured ring).
        let method_cli = arg(&a, "--method");
        if let Some(m) = &method_cli {
            if !["nearest", "timealign", "minphase", "notchwarp"].contains(&m.as_str()) {
                return Err(format!("--method must be nearest|timealign|minphase|notchwarp; got {m:?}").into());
            }
        }
        let interp_step: f64 = arg_num::<f64>(&a, "--interp-step")?.unwrap_or(15.0).clamp(1.0, 90.0);
        let rotate_demo = flag(&a, "--rotate-demo");
        // Install-relative asset resolution (no hardcoded paths): CLI → env → <exe>/… → %APPDATA%.
        let web_dir =
            assets::resolve_dir(arg(&a, "--web-dir").as_deref(), "WLBS_WEB_DIR", "web_cockpit")
                .to_string_lossy()
                .into_owned();
        let run_secs: f64 = arg_num(&a, "--secs")?.unwrap_or(0.0); // 0 = forever
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
        // Clamp stride to ≥1: 0 would make `is_multiple_of(0)` disable all pose updates (BUG-31).
        let pose_stride: usize = arg_num(&a, "--pose-stride")?.unwrap_or(2).max(1);
        // Camera id string (decimal index or an opaque nokhwa string index), not parsed to u32, so a
        // string-indexed camera can be selected too (BUG-34).
        let camera_id: Option<String> = arg(&a, "--camera");
        let no_camera = flag(&a, "--no-camera"); // privacy opt-out of the auto webcam (BUG-10)
        // Optional explicit capture format (BUG-28): --resolution WxH, --framerate N (bad → error).
        let cli_res: Option<(u32, u32)> = match arg(&a, "--resolution") {
            None => None,
            Some(s) => {
                let (w, h) = s
                    .split_once(['x', 'X', '×'])
                    .ok_or_else(|| format!("invalid --resolution {s:?} (want WxH)"))?;
                let w = w.trim().parse().map_err(|_| format!("invalid --resolution width in {s:?}"))?;
                let h = h.trim().parse().map_err(|_| format!("invalid --resolution height in {s:?}"))?;
                Some((w, h))
            }
        };
        let cli_fps: Option<u32> = arg_num(&a, "--framerate")?;
        // Enumerate local cameras once, for the cockpit picker and the auto-default source.
        let cameras = crate::vision::enumerate_cameras();
        for (id, n) in &cameras {
            println!("CAMERA {id} {n}");
        }
        let cameras_json =
            serde_json::Value::Array(cameras.iter().map(|(id, n)| serde_json::json!([n, id])).collect());

        // Startup asset existence check (BUG-24): surface missing dirs/files with a clear log line up
        // front, instead of a 404 at the browser or a failure buried in the vision thread. These are
        // warnings, not fatal — the engine still runs (held yaw, blank preview) if the camera assets
        // are absent; the pack load below is the one hard requirement.
        if !std::path::Path::new(&web_dir).is_dir() {
            eprintln!("ASSET_WARN web_dir not found: {web_dir} (the cockpit page will 404)");
        }
        for f in ["facedet.onnx", "facemesh.onnx"] {
            let p = std::path::Path::new(&onnx_dir).join(f);
            if !p.is_file() {
                eprintln!("ASSET_WARN missing {} (head tracking will be unavailable)", p.display());
            }
        }
        if !std::path::Path::new(&canonical).is_file() {
            eprintln!("ASSET_WARN canonical model not found: {canonical} (head tracking will fail)");
        }

        let pack = Arc::new(BrirPack::load(&brir_dir)?);
        println!("PACK sr={} taps={} ring={} lfe_idx={}", pack.samplerate, pack.n_taps, pack.n_ring, pack.lfe_idx);

        // --- live settings: CLI/computed defaults, then the persisted overlay (BUG-08) ---
        let store = SettingsStore::new(assets::appdata_wlbs().join("settings.json"));
        let defaults = Settings {
            method: "timealign".into(), // the owner's preferred method (#187)
            hrir_name: arg(&a, "--pack").unwrap_or_else(|| "default".into()),
            backend: "rust".into(),
            comp: "off".into(),
            block,
            cushion_ms: latency_ms,
            exclusive: false,
            hp_eq: false,
            hp_model: None,
            predict: false,
            camera: camera_id.clone(),
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
        if let Some(m) = method_cli.clone() { settings.method = m; }
        if camera_id.is_some() { settings.camera = camera_id.clone(); }
        if let Some((w, h)) = cli_res { settings.cam_w = w; settings.cam_h = h; }
        if let Some(f) = cli_fps { settings.cam_fps = f; }
        let hrir_list: Vec<String> = packs::list_packs().into_iter().map(|p| p.name).collect();
        let mut block = settings.block; // the live block size (the settings dialog can change it)

        // Persist off the real-time capture thread: an `fsync` can stall tens of ms, which on the
        // capture thread would be an audio dropout. The thread sends settings JSON to this saver.
        let (persist_tx, persist_rx) = std::sync::mpsc::channel::<serde_json::Value>();
        let persist_handle = std::thread::spawn(move || {
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

        // Ctrl-C flips `running` so the threads wind down and the WASAPI devices are released cleanly
        // instead of being leaked (BUG-15). (On a Windows console-close the handler fires but may not
        // finish the join before the OS reaps the process — Ctrl-C / `--secs` expiry is the clean path.)
        {
            let running = Arc::clone(&running);
            if let Err(e) = ctrlc::set_handler(move || running.store(false, Ordering::Relaxed)) {
                eprintln!("CTRLC handler not installed: {e}");
            }
        }

        // --- control-plane cockpit (tokio) ---
        let cockpit = Cockpit::new(web_dir, build_config(&settings, &cameras_json, &hrir_list, pack.samplerate));
        {
            let cockpit = Arc::clone(&cockpit);
            std::thread::spawn(move || control_plane::serve_blocking(cockpit, port));
        }

        // --- head-pose source --- priority: explicit --camera > explicit mjpeg/image/demo >
        // persisted camera > first local camera (the product default) > held. A persisted camera must
        // NOT override an explicit `--rotate-demo`/`--face-image`/`--camera-mjpeg` (WSL + test flows).
        // `--no-camera` disables the auto webcam entirely (privacy opt-out, BUG-10); an explicit
        // `--camera` still forces a specific one.
        let auto_cam: Option<String> = if let Some(c) = camera_id.clone() {
            Some(c)
        } else if no_camera || camera_mjpeg.is_some() || face_image.is_some() || rotate_demo {
            None
        } else {
            settings.camera.clone().or_else(|| cameras.first().map(|(id, _)| id.clone()))
        };
        let pose_source: &str;
        if let Some(id) = auto_cam {
            pose_source = "camera";
            crate::vision::spawn_camera_pose(
                id, onnx_dir, canonical, pose_stride,
                settings.cam_w, settings.cam_h, settings.cam_fps,
                Arc::clone(&pose), Arc::clone(&running), Some(Arc::clone(&cockpit)),
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
        // The endpoint must run at the source/pack rate (checked in output_loop). On ANY output exit
        // — error or clean — `running` is flipped so the render loop stops filling a FIFO nobody
        // drains ("running but no sound"), and surfaces the error to telemetry (BUG-04).
        let output_handle = {
            let fifo = Arc::clone(&fifo);
            let running = Arc::clone(&running);
            let output = output.clone();
            let out_status = Arc::clone(&out_status);
            let expected_sr = pack.samplerate;
            std::thread::spawn(move || {
                if let Err(e) = output_loop(&output, fifo, Arc::clone(&running), block, expected_sr, &out_status) {
                    eprintln!("OUTPUT_ERROR {e}");
                    out_status.lock().unwrap().err = Some(e);
                }
                running.store(false, Ordering::Relaxed);
            })
        };

        // --- capture + render thread (this thread) ---
        init_com()?;
        let mmcss = set_pro_audio();
        println!("MMCSS_PRO_AUDIO {mmcss}");
        let mut src = WasapiLoopback::open(&source)?;
        let sfmt = src.format();
        println!("SOURCE {:?} sr={} ch={}", source, sfmt.sample_rate, sfmt.channels);
        let n_in = sfmt.channels as usize;

        // Sample-rate reconciliation (BUG-03): the IRs are baked at the pack rate, so convolving a
        // source at a different rate warps ITD/pitch and miscomputes the cushion/fade. No resampler
        // yet → hard-error with a clear message rather than silently spatialising at the wrong rate.
        if sfmt.sample_rate != pack.samplerate {
            running.store(false, Ordering::Relaxed);
            return Err(format!(
                "source is {} Hz but the BRIR pack is {} Hz — use a matching-rate pack (no resampler yet)",
                sfmt.sample_rate, pack.samplerate
            )
            .into());
        }
        if n_in == 6 {
            println!("SOURCE 5.1 assumed layout L,R,C,LFE,SL,SR (side surrounds)");
        }

        let mut render = build_render(&pack, n_in, block, latency_ms, &settings.method, interp_step)?;

        let start = Instant::now();
        let mut in_acc: Vec<f32> = Vec::with_capacity(block * n_in * 4);
        let mut buf: Vec<f32> = Vec::with_capacity(block * n_in * 4);
        let mut pcm64: Vec<f64> = Vec::with_capacity(block * n_in); // reused render input (BUG-13)
        let mut out64: Vec<f64> = vec![0.0; block * 2]; // reused render output (BUG-13)
        let mut last_tele = Instant::now();
        let mut last_audio = Instant::now();
        let mut rendered_blocks = 0u64;
        let mut render_ema = 0.0f64; // EMA of per-block render time as % of the block budget
        let mut render_peak = 0.0f64;
        let mut lat = LatencyHistogram::new(120, 20, 0.0, 120.0);
        let mut snapshot: Option<Settings> = None; // live edit-session snapshot (begin/commit/cancel)
        let mut read_errs = 0u32; // consecutive capture-read errors (transient → retry; BUG-14)
        let mut discontinuities = 0u64; // device-signalled capture glitches (BUG-33)
        let mut source_silent = false; // last capture buffer was device-flagged silent (BUG-33)
        while running.load(Ordering::Relaxed) {
            if run_secs > 0.0 && start.elapsed().as_secs_f64() >= run_secs {
                break;
            }

            // --- drain + apply cockpit controls (BUG-01) ---
            let block_before = settings.block;
            let cush_before = settings.cushion_ms;
            let method_before = settings.method.clone();
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
                    "set_camera" => {
                        // The picker sends the camera id as a string; tolerate a legacy numeric value.
                        let id = a0.and_then(|v| v.as_str().map(String::from).or_else(|| v.as_u64().map(|n| n.to_string())));
                        if let Some(id) = id { settings.camera = Some(id); dirty = true; }
                    }
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
                // Block/cushion/method are the knobs re-applied to the live audio graph here on the
                // capture thread (hrir/exclusive/camera need a restart — persisted above). A block or
                // method change rebuilds the convolver (`render`); a cushion-only change rebuilds just
                // the FIFO. NOTE: a synthesis-method rebuild runs the ring interpolation inline (~0.3 s
                // for timealign on the 24k-tap Audeze set), so switching method mid-stream causes a
                // brief capture gap + FIFO re-prime — acceptable for a deliberate dropdown change, the
                // same rebuild mechanism block/cushion use.
                let block_changed = settings.block != block_before;
                let method_changed = settings.method != method_before;
                if block_changed || method_changed || settings.cushion_ms != cush_before {
                    let new_block = settings.block;
                    let cushion = cushion_blocks(settings.cushion_ms, pack.samplerate, new_block);
                    let render_ok = if block_changed || method_changed {
                        match build_render(&pack, n_in, new_block, latency_ms, &settings.method, interp_step) {
                            Ok(r) => { render = r; true }
                            Err(e) => {
                                eprintln!("RECONFIG_ERROR {e}");
                                settings.block = block_before;
                                settings.method = method_before.clone();
                                false
                            }
                        }
                    } else {
                        true
                    };
                    if render_ok {
                        block = settings.block; // only advance the live block once render matches it
                        *lock_fifo(&fifo) =
                            CushionFifo::new(cushion, cushion * 2, vec![0.0f32; block * 2]);
                        in_acc.clear();
                        println!("RECONFIG block={block} cushion={cushion} method={}", settings.method);
                    }
                }
            }

            buf.clear();
            // A capture timeout no longer skips telemetry: we fall through to the heartbeat below so
            // an idle source keeps the cockpit live (BUG-05). A short timeout keeps the control drain
            // and heartbeat responsive (~20 Hz) even when an idle loopback emits no capture events.
            // A transient read error is retried rather than aborting the whole engine (BUG-14); only
            // a sustained run of errors (device truly gone) gives up.
            let flags = match src.read(&mut buf, 50) {
                Ok(f) => { read_errs = 0; f }
                Err(e) => {
                    read_errs += 1;
                    eprintln!("CAPTURE_READ_ERROR ({read_errs}) {e}");
                    if read_errs >= 200 {
                        running.store(false, Ordering::Relaxed);
                        return Err(format!("capture failed {read_errs}× in a row: {e}").into());
                    }
                    std::thread::sleep(Duration::from_millis(5));
                    continue;
                }
            };
            if let Some(flags) = flags {
                if flags.discontinuity {
                    discontinuities += 1; // device-signalled glitch (BUG-33) — surfaced in telemetry
                }
                source_silent = flags.silent; // device-flagged digital silence (BUG-33)
                last_audio = Instant::now();
                in_acc.extend_from_slice(&buf);
                // Sample the head yaw now, after the blocking read, so the azimuths the block renders
                // with are fresh (not up to a device period / timeout stale). The azimuth layout
                // depends on the channel count (5.1 side-surrounds differ from the 7.1 rears).
                let yaw = *pose.yaw.lock().unwrap();
                let az: Vec<f64> = match n_in {
                    6 => head_relative_azimuths_5p1(yaw, yaw_sign).to_vec(),
                    _ => head_relative_azimuths(yaw, yaw_sign)[..n_in.min(8)].to_vec(),
                };
                let block_time = block as f64 / pack.samplerate.max(1) as f64;
                while in_acc.len() >= block * n_in {
                    // Reuse the transient scratch across blocks (BUG-13): drain straight into the f64
                    // input buffer and render into a resident output buffer; only the FIFO payload
                    // (`stereo`, which the FIFO owns) is freshly allocated.
                    pcm64.clear();
                    pcm64.extend(in_acc.drain(..block * n_in).map(|v| v as f64));
                    out64.resize(block * 2, 0.0);
                    let t0 = Instant::now();
                    render.process_into(&pcm64, &az, &mut out64);
                    let pct = if block_time > 0.0 { t0.elapsed().as_secs_f64() / block_time * 100.0 } else { 0.0 };
                    render_ema = render_ema * 0.9 + pct * 0.1;
                    if pct > render_peak { render_peak = pct; }
                    // Clamp to [-1,1] and drop non-finite samples before the device: the Float32
                    // output path does no clamping, so an overshoot would clip and a stray NaN/Inf
                    // (e.g. from a bad render) would reach the endpoint (BUG-21).
                    let stereo: StereoBlock = out64
                        .iter()
                        .map(|&v| {
                            let s = v as f32;
                            if s.is_finite() { s.clamp(-1.0, 1.0) } else { 0.0 }
                        })
                        .collect();
                    lock_fifo(&fifo).push(stereo);
                    rendered_blocks += 1;
                }
            }

            // --- telemetry heartbeat (time-driven, runs even when the source is idle) ---
            if last_tele.elapsed() >= Duration::from_millis(50) {
                let cur_yaw = *pose.yaw.lock().unwrap();
                let source_idle = last_audio.elapsed() >= Duration::from_millis(250);
                render_peak = (render_peak * 0.95).max(render_ema); // slow decay toward the mean
                let (cushion_now, fill_now, st) = {
                    let f = lock_fifo(&fifo);
                    (f.cushion(), f.len(), f.stats())
                };
                lat.push(telemetry::estimate_total_ms(block, pack.samplerate, cushion_now, render_ema));
                let os = out_status.lock().unwrap().clone();
                let cam_error = pose.cam_error();
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
                    discontinuities,
                    rendered_blocks,
                    render_load_pct: render_ema,
                    render_peak_pct: render_peak,
                    source_idle,
                    source_silent,
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
                    cam_error: cam_error.as_deref(),
                    mmcss,
                    lat: &lat,
                });
                cockpit.push_state(telemetry::wrap_state(state));
                last_tele = Instant::now();
            }
        }
        running.store(false, Ordering::Relaxed);
        let _ = src.stop();
        // Join the output thread so its WASAPI render device is released before exit (BUG-15); it
        // notices `running==false` within one wait timeout (≤1 s). Pose/camera threads are left to
        // exit on `running` — interrupting a blocked camera read is BUG-30 (vision batch).
        let _ = output_handle.join();
        // Flush any pending save (e.g. a settings_commit sent just before shutdown) before exit.
        drop(persist_tx);
        let _ = persist_handle.join();
        println!("DONE rendered_blocks={rendered_blocks} elapsed_s={:.2}", start.elapsed().as_secs_f64());
        Ok(())
    }

    /// Output-thread status shared with the capture thread for cockpit health telemetry: the opened
    /// endpoint's format and any fatal error. The output thread also flips `running` on exit (BUG-04),
    /// so a dead output stops the render loop; this struct carries the error text to the cockpit.
    #[derive(Default, Clone)]
    struct OutputStatus {
        sr: u32,
        #[allow(dead_code)]
        ch: u16,
        err: Option<String>,
    }

    /// Build the world-lock renderer from the pack, adapting to the source channel count (8 = full
    /// 7.1 ring + LFE; 6 = 5.1 ring + LFE; 2 = front L/R only, for a stereo source). The directional
    /// inputs steer over a ring grid: for `nearest` that's the measured 7-speaker ring (cheapest);
    /// for the synthesis methods it's the dense interpolated ring (`interp`, every `step_deg`), so a
    /// head turn moves the phantom smoothly through the in-between directions (#187). The grid is
    /// built once here (off the realtime path); `GridRender` itself stays allocation-free per block.
    fn build_render(
        pack: &BrirPack, n_in: usize, block: usize, latency_ms: f64, method: &str, step_deg: f64,
    ) -> Result<GridRender, String> {
        let fade = (latency_ms * 1e-3 * pack.samplerate as f64).round().max(1.0) as i64;
        // Steering grid: measured ring for nearest, dense interpolated ring otherwise. `taps` comes
        // from the grid itself (the interpolation preserves the pack's tap count).
        let (grid_l, grid_r, grid_az, n_grid, taps) = if method == "nearest" {
            (pack.ring_left.clone(), pack.ring_right.clone(), pack.ring_az.clone(), pack.n_ring, pack.n_taps)
        } else {
            let d = crate::interp::interpolated_ring(
                &pack.ring_left, &pack.ring_right, &pack.ring_az, pack.n_ring, pack.n_taps,
                pack.samplerate, method, step_deg, true,
            );
            (d.left, d.right, d.az, d.naz, d.n_taps)
        };
        match n_in {
            // 7.1: the 7 directional input columns steer over the grid; LFE (col 3) its own convolver.
            8 => {
                let lfe = Lfe::Brir {
                    conv: BusConvolverCore::new(&pack.lfe_left, &pack.lfe_right, 1, taps, block)?,
                    idx: pack.lfe_idx,
                };
                GridRender::new(&grid_l, &grid_r, n_grid, taps, grid_az, pack.dir_idx.clone(), 8, fade, block, lfe)
            }
            // 5.1 interleave (L,R,C,LFE,SL,SR): directional input columns 0,1,2,4,5; LFE col 3.
            6 => {
                let lfe = Lfe::Brir {
                    conv: BusConvolverCore::new(&pack.lfe_left, &pack.lfe_right, 1, taps, block)?,
                    idx: 3,
                };
                GridRender::new(&grid_l, &grid_r, n_grid, taps, grid_az, vec![0, 1, 2, 4, 5], 6, fade, block, lfe)
            }
            // Stereo: L,R over the full ring grid (BUG-27 — no 2-point collapse).
            2 => GridRender::new(&grid_l, &grid_r, n_grid, taps, grid_az, vec![0, 1], 2, fade, block, Lfe::None),
            other => Err(format!("source has {other} channels; engine handles 8 (7.1), 6 (5.1), or 2 (stereo)")),
        }
    }

    /// Lock the FIFO, recovering from a poisoned lock instead of propagating the panic. A panic in
    /// either audio thread must not cascade into the other via a poisoned mutex (BUG-23); the FIFO's
    /// own state stays valid across a panic, so `into_inner` is safe here.
    fn lock_fifo(fifo: &Mutex<CushionFifo<StereoBlock>>) -> std::sync::MutexGuard<'_, CushionFifo<StereoBlock>> {
        fifo.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// Pull stereo blocks from the FIFO and write them to the output device each event. `expected_sr`
    /// is the source/pack sample rate: the output endpoint must run at the same rate, or the FIFO
    /// would drift continuously (no resampler) — a hard error rather than a silent stream of dropouts
    /// (BUG-03). Output endpoints with more than two channels are handled by placing the binaural L/R
    /// in the first two channels and zeroing the rest (BUG-19).
    fn output_loop(
        output_name: &str,
        fifo: Arc<Mutex<CushionFifo<StereoBlock>>>,
        running: Arc<AtomicBool>,
        block: usize,
        expected_sr: u32,
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
        let out_ch = ofmt.channels as usize;
        if out_ch < 2 {
            return Err(format!("output endpoint is {out_ch}ch; need at least a stereo endpoint"));
        }
        if ofmt.sample_rate != expected_sr {
            return Err(format!(
                "output endpoint is {} Hz but the source/pack is {expected_sr} Hz — match the endpoints (no resampler yet)",
                ofmt.sample_rate
            ));
        }
        let mut pending: Vec<f32> = Vec::with_capacity(block * 4);
        let mut frame_buf: Vec<f32> = Vec::with_capacity(block * out_ch);
        while running.load(Ordering::Relaxed) {
            if !out.wait(1000)? {
                continue;
            }
            let need = out.frames_needed()?;
            while pending.len() / 2 < need {
                let (_p, blk) = lock_fifo(&fifo).pull();
                pending.extend_from_slice(&blk.unwrap_or_else(|| vec![0.0f32; block * 2]));
            }
            if out_ch == 2 {
                let take = need * 2;
                out.write(&pending[..take])?;
                pending.drain(..take);
            } else {
                // Expand each stereo frame to the endpoint's channel count: L, R, then silence.
                frame_buf.clear();
                for f in 0..need {
                    frame_buf.push(pending[f * 2]);
                    frame_buf.push(pending[f * 2 + 1]);
                    frame_buf.extend(std::iter::repeat(0.0).take(out_ch - 2));
                }
                out.write(&frame_buf)?;
                pending.drain(..need * 2);
            }
        }
        let _ = out.stop();
        Ok(())
    }
}
