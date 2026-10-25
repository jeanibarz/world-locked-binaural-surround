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

mod geometry;
mod pack;

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
    use control_plane::Cockpit;
    use render_core_lib::{GridRender, Lfe};

    use crate::geometry::head_relative_azimuths;
    use crate::pack::BrirPack;

    fn arg(a: &[String], k: &str) -> Option<String> {
        a.iter().position(|x| x == k).and_then(|i| a.get(i + 1)).cloned()
    }
    fn flag(a: &[String], k: &str) -> bool {
        a.iter().any(|x| x == k)
    }

    type StereoBlock = Vec<f32>; // interleaved L,R, length block*2

    pub fn run() -> Result<(), Box<dyn std::error::Error>> {
        let a: Vec<String> = std::env::args().collect();
        let brir_dir = PathBuf::from(
            arg(&a, "--brir-dir").expect("--brir-dir <baked 7.1 dir> required"),
        );
        let source = arg(&a, "--source").unwrap_or_else(|| "VoiceMeeter".into());
        let output = arg(&a, "--output").unwrap_or_else(|| "Bigscreen".into());
        let port: u16 = arg(&a, "--port").and_then(|s| s.parse().ok()).unwrap_or(8732);
        let block: usize = arg(&a, "--block").and_then(|s| s.parse().ok()).unwrap_or(256);
        let latency_ms: f64 = arg(&a, "--latency-ms").and_then(|s| s.parse().ok()).unwrap_or(20.0);
        let yaw_sign: f64 = arg(&a, "--yaw-sign").and_then(|s| s.parse().ok()).unwrap_or(1.0);
        let rotate_demo = flag(&a, "--rotate-demo");
        let web_dir = arg(&a, "--web-dir").unwrap_or_else(|| "apps/web_cockpit".into());
        let run_secs: f64 = arg(&a, "--secs").and_then(|s| s.parse().ok()).unwrap_or(0.0); // 0 = forever

        let pack = Arc::new(BrirPack::load(&brir_dir)?);
        println!("PACK sr={} taps={} ring={} lfe_idx={}", pack.samplerate, pack.n_taps, pack.n_ring, pack.lfe_idx);

        // Shared state.
        let yaw = Arc::new(Mutex::new(0.0f64));
        let fifo: Arc<Mutex<CushionFifo<StereoBlock>>> = {
            let cushion = cushion_blocks(latency_ms, pack.samplerate, block);
            Arc::new(Mutex::new(CushionFifo::new(cushion, cushion * 2, vec![0.0f32; block * 2])))
        };
        let running = Arc::new(AtomicBool::new(true));

        // --- control-plane cockpit (tokio) ---
        let cockpit = Cockpit::new(
            web_dir,
            serde_json::json!({
                "cameras": [], "hrir": ["mine"], "methods": ["nearest"],
                "resolutions": ["default"], "framerates": ["default"], "samplerate": 48000,
                "win_w": 1320, "win_h": 864, "method": "nearest", "backend": "rust", "block": block
            }),
        );
        {
            let cockpit = Arc::clone(&cockpit);
            std::thread::spawn(move || control_plane::serve_blocking(cockpit, port));
        }

        // --- demo yaw rotator (stands in for the camera until stage B) ---
        if rotate_demo {
            let yaw = Arc::clone(&yaw);
            let running = Arc::clone(&running);
            std::thread::spawn(move || {
                let t0 = Instant::now();
                while running.load(Ordering::Relaxed) {
                    // sweep ±40° at ~0.1 Hz so the world-lock is audible/visible
                    *yaw.lock().unwrap() = 40.0 * (t0.elapsed().as_secs_f64() * 0.2).sin();
                    std::thread::sleep(Duration::from_millis(20));
                }
            });
        }

        // --- output thread: drain the FIFO to the render endpoint ---
        {
            let fifo = Arc::clone(&fifo);
            let running = Arc::clone(&running);
            let output = output.clone();
            std::thread::spawn(move || {
                if let Err(e) = output_loop(&output, fifo, running, block) {
                    eprintln!("OUTPUT_ERROR {e}");
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
        let mut rendered_blocks = 0u64;
        while running.load(Ordering::Relaxed) {
            if run_secs > 0.0 && start.elapsed().as_secs_f64() >= run_secs {
                break;
            }
            buf.clear();
            if src.read(&mut buf, 1000)?.is_none() {
                continue; // capture event timed out
            }
            in_acc.extend_from_slice(&buf);
            let cur_yaw = *yaw.lock().unwrap();
            let az = head_relative_azimuths(cur_yaw, yaw_sign);
            while in_acc.len() >= block * n_in {
                let pcm: Vec<f32> = in_acc.drain(..block * n_in).collect();
                let pcm64: Vec<f64> = pcm.iter().map(|&v| v as f64).collect();
                let mut out = vec![0.0f64; block * 2];
                render.process_into(&pcm64, &az[..n_in.min(8)], &mut out);
                let stereo: StereoBlock = out.iter().map(|&v| v as f32).collect();
                fifo.lock().unwrap().push(stereo);
                rendered_blocks += 1;
            }
            if last_tele.elapsed() >= Duration::from_millis(50) {
                let st = fifo.lock().unwrap().stats();
                cockpit.push_state(
                    serde_json::json!({
                        "type": "state",
                        "pose": {"yaw": cur_yaw, "pitch": 0.0, "roll": 0.0},
                        "engine": "rust", "rendered_blocks": rendered_blocks,
                        "fifo": {"underruns": st.underruns, "overflow_drops": st.overflow_drops}
                    })
                    .to_string(),
                );
                last_tele = Instant::now();
            }
        }
        running.store(false, Ordering::Relaxed);
        let _ = src.stop();
        println!("DONE rendered_blocks={rendered_blocks} elapsed_s={:.2}", start.elapsed().as_secs_f64());
        Ok(())
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
    ) -> Result<(), String> {
        init_com()?;
        set_pro_audio();
        let mut out = WasapiRender::open(output_name)?;
        let ofmt = out.format();
        println!("OUTPUT {:?} sr={} ch={}", output_name, ofmt.sample_rate, ofmt.channels);
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
