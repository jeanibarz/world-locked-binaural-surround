//! `audio_probe` — host measurement for the `audio-io` slice (Windows-only).
//!
//! **Loopback capture ONLY** (no render output), so it never plays into the owner's device while
//! they are listening — the R-S2 discipline. It measures, over `--secs`:
//!   * capture robustness: frames, event-timeouts, discontinuities, silent buffers, max callback gap;
//!   * render-clock drift vs wall clock (ppm);
//!   * whether MMCSS "Pro Audio" elevation succeeds from Rust;
//!   * the cushioned drift-FIFO driven by the live stream: captured frames feed the FIFO as render
//!     blocks (the "worker"), a wall-clock-paced drain pulls blocks at the nominal rate (the output
//!     clock), and it reports underruns / re-primes / overflow-drops / mean fill — showing the
//!     journal-81 cushion rides the drift without an audio device attached.
//!
//! Usage: `audio_probe [secs] [device-name-substr] [block-frames] [latency-ms] [stall-ms]`
//!   e.g. `audio_probe 12 "VoiceMeeter Input" 512 40 300`
//! `stall-ms` pauses capture once after three seconds to test backlog recovery (default: 0).

#[cfg(not(windows))]
fn main() {
    eprintln!("audio_probe is Windows-only (WASAPI). Build for x86_64-pc-windows-msvc.");
}

#[cfg(windows)]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    use std::time::Instant;

    use audio_io::wasapi_backend::{init_com, list_render_endpoints, set_pro_audio, WasapiLoopback};
    use audio_io::{cushion_blocks, AudioSource, CushionFifo};

    let args: Vec<String> = std::env::args().collect();
    let secs: f64 = args.get(1).and_then(|s| s.parse().ok()).unwrap_or(30.0);
    let want = args.get(2).cloned().unwrap_or_else(|| "FiiO".to_string());
    let block: usize = args.get(3).and_then(|s| s.parse().ok()).unwrap_or(256);
    let latency_ms: f64 = args.get(4).and_then(|s| s.parse().ok()).unwrap_or(20.0);

    // Optional one-shot capture-thread stall, reproducing an inline renderer rebuild.
    let stall_ms: u64 = args.get(5).and_then(|s| s.parse().ok()).unwrap_or(0);
    let mut stalled = false;
    init_com()?;
    let mmcss = set_pro_audio();
    println!("MMCSS_PRO_AUDIO {mmcss}");
    if let Ok(eps) = list_render_endpoints() {
        println!("RENDER_ENDPOINTS {eps:?}");
    }

    let mut src = WasapiLoopback::open(&want)?;
    let fmt = src.format();
    let sr = fmt.sample_rate as f64;
    let ch = fmt.channels as usize;
    println!("TARGET_DEVICE {want:?}  sr={} ch={} block={} latency_ms={}", fmt.sample_rate, ch, block, latency_ms);

    let cushion = cushion_blocks(latency_ms, fmt.sample_rate, block);
    let mut fifo: CushionFifo<()> = CushionFifo::new(cushion, cushion * 2, ());
    println!("FIFO cushion={cushion} max_backlog={}", cushion * 2);

    let start = Instant::now();
    let (mut total_frames, mut timeouts, mut discont, mut silent, mut reads) = (0u64, 0u64, 0u64, 0u64, 0u64);
    let mut last = Instant::now();
    let mut max_gap_ms = 0.0f64;
    let mut carry = 0usize; // captured frames not yet formed into a block
    let mut pulled = 0u64; // blocks drained so far (paced by wall clock)
    let mut fill_sum = 0u64; // for mean fill (sampled per pull)
    let block_dt = block as f64 / sr; // nominal output-block period (s)
    let mut buf: Vec<f32> = Vec::with_capacity(block * ch * 4);

    let mut window_start = Instant::now();
    let (mut window_frames, mut window_discont) = (0u64, 0u64);
    while start.elapsed().as_secs_f64() < secs {
        if !stalled && stall_ms > 0 && start.elapsed().as_secs_f64() >= 3.0 {
            println!("INJECT_STALL ms={stall_ms}");
            std::thread::sleep(std::time::Duration::from_millis(stall_ms));
            stalled = true;
        }
        buf.clear();
        match src.read(&mut buf, 1000)? {
            None => {
                timeouts += 1;
                continue;
            }
            Some(flags) => {
                reads += 1;
                if flags.discontinuity {
                    discont += 1;
                }
                if flags.silent {
                    silent += 1;
                }
            }
        }
        let frames = buf.len() / ch;
        total_frames += frames as u64;

        // The render worker: form whole render blocks from the captured frames and push them.
        carry += frames;
        while carry >= block {
            fifo.push(());
            carry -= block;
        }

        // The output clock: drain one block per nominal block period elapsed (wall clock).
        let due = (start.elapsed().as_secs_f64() / block_dt) as u64;
        while pulled < due {
            let (p, _) = fifo.pull();
            let _ = p; // Pull::{Block,Underrun,Repriming} tallied in fifo.stats()
            fill_sum += fifo.len() as u64;
            pulled += 1;
        }

        if window_start.elapsed().as_secs_f64() >= 1.0 {
            println!("WINDOW t={:.2} rate_hz={:.1} discontinuities={} frames_per_read={frames}",
                start.elapsed().as_secs_f64(),
                (total_frames - window_frames) as f64 / window_start.elapsed().as_secs_f64(),
                discont - window_discont);
            window_frames = total_frames;
            window_discont = discont;
            window_start = Instant::now();
        }
        let now = Instant::now();
        let gap = (now - last).as_secs_f64() * 1000.0;
        if gap > max_gap_ms {
            max_gap_ms = gap;
        }
        last = now;
    }

    let _ = src.stop();
    let elapsed = start.elapsed().as_secs_f64();
    let eff_rate = total_frames as f64 / elapsed;
    let drift_ppm = (eff_rate / sr - 1.0) * 1e6;
    let st = fifo.stats();
    let mean_fill = if pulled > 0 { fill_sum as f64 / pulled as f64 } else { 0.0 };

    println!("CAPTURED frames={total_frames} elapsed_s={elapsed:.3} eff_rate_hz={eff_rate:.2} drift_ppm={drift_ppm:.1}");
    println!("ROBUSTNESS reads={reads} event_timeouts={timeouts} discontinuities={discont} silent_buffers={silent} max_gap_ms={max_gap_ms:.1}");
    println!(
        "FIFO_RESULT pulls={} underruns={} reprimes={} reprime_silent_blocks={} overflow_drops={} mean_fill_blocks={:.2} (cushion={})",
        st.pulls, st.underruns, st.reprimes, st.reprime_silent_blocks, st.overflow_drops, mean_fill, cushion
    );
    Ok(())
}
