// R-S2 probe: WASAPI loopback capture + MMCSS "Pro Audio" from Rust, measuring clock drift and
// capture robustness under sustained load — the feasibility gate for a pure-Rust audio engine.
//
// Loopback-capture ONLY (no render output), so it never plays into the owner's device while they
// are listening. Measures: the render-device clock drift vs wall clock (ppm), capture
// discontinuities (WASAPI's own glitch flag), silent buffers, max callback gap, and whether MMCSS
// "Pro Audio" elevation succeeds from Rust (the engine's render thread relies on it today).
use std::collections::VecDeque;
use std::time::Instant;

use wasapi::*;

#[link(name = "avrt")]
extern "system" {
    fn AvSetMmThreadCharacteristicsW(task: *const u16, taskindex: *mut u32) -> isize;
}

fn set_pro_audio() -> bool {
    let name: Vec<u16> = "Pro Audio".encode_utf16().chain(std::iter::once(0)).collect();
    let mut idx: u32 = 0;
    let h = unsafe { AvSetMmThreadCharacteristicsW(name.as_ptr(), &mut idx) };
    h != 0
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let secs: f64 = std::env::args().nth(1).and_then(|s| s.parse().ok()).unwrap_or(30.0);
    initialize_mta().unwrap();
    let mmcss = set_pro_audio();

    let want = std::env::args().nth(2).unwrap_or_else(|| "FiiO".to_string());
    let enumerator = DeviceEnumerator::new()?;
    // The Windows DEFAULT render endpoint can be an idle monitor; loopback on an idle endpoint
    // yields no packets. Target an ACTIVE endpoint by friendly-name substring (default "FiiO" = the
    // engine's output = what the owner hears).
    let coll = enumerator.get_device_collection(&Direction::Render)?;
    let n = coll.get_nbr_devices()?;
    let mut chosen = None;
    for i in 0..n {
        let d = coll.get_device_at_index(i)?;
        let name = d.get_friendlyname().unwrap_or_default();
        if name.to_lowercase().contains(&want.to_lowercase()) {
            println!("TARGET_DEVICE {:?}", name);
            chosen = Some(d);
            break;
        }
    }
    let device = chosen.ok_or_else(|| format!("no render device matching {want:?}"))?;
    let mut client = device.get_iaudioclient()?;
    let format = client.get_mixformat()?;
    let sr = format.get_samplespersec() as f64;
    let ch = format.get_nchannels();
    let blockalign = format.get_blockalign() as usize;
    let (_def, min_time) = client.get_device_period()?;
    let mode = StreamMode::EventsShared { autoconvert: false, buffer_duration_hns: min_time };
    client.initialize_client(&format, &Direction::Capture, &mode)?;
    let h_event = client.set_get_eventhandle()?;
    let capture = client.get_audiocaptureclient()?;

    let mut q: VecDeque<u8> = VecDeque::new();
    client.start_stream()?;
    let start = Instant::now();
    let (mut total_bytes, mut timeouts, mut discont, mut silent, mut reads) = (0u64, 0u64, 0u64, 0u64, 0u64);
    let mut last = Instant::now();
    let mut max_gap_ms = 0f64;

    while start.elapsed().as_secs_f64() < secs {
        if h_event.wait_for_event(1000).is_err() {
            timeouts += 1;
            continue;
        }
        let before = q.len();
        let info = capture.read_from_device_to_deque(&mut q)?;
        total_bytes += (q.len() - before) as u64;
        reads += 1;
        if info.flags.data_discontinuity {
            discont += 1;
        }
        if info.flags.silent {
            silent += 1;
        }
        let now = Instant::now();
        let dt = (now - last).as_secs_f64() * 1000.0;
        if dt > max_gap_ms {
            max_gap_ms = dt;
        }
        last = now;
        q.clear(); // count only; retain no audio (non-disruptive, no playback)
    }

    let elapsed = start.elapsed().as_secs_f64();
    let frames = total_bytes / blockalign as u64;
    let eff_rate = frames as f64 / elapsed;
    let drift_ppm = (eff_rate / sr - 1.0) * 1e6;
    println!("MMCSS_PRO_AUDIO {}", mmcss);
    println!("DEVICE sr={} ch={} blockalign={}", sr, ch, blockalign);
    println!(
        "CAPTURED frames={} elapsed_s={:.3} eff_rate_hz={:.2} drift_ppm={:.1}",
        frames, elapsed, eff_rate, drift_ppm
    );
    println!(
        "ROBUSTNESS reads={} event_timeouts={} discontinuities={} silent_buffers={} max_gap_ms={:.1}",
        reads, timeouts, discont, silent, max_gap_ms
    );
    Ok(())
}
