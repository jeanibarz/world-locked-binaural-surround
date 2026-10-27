//! Measure optional trace submission on the target OS without opening an audio device.
//! Both modes are sampled in each iteration; the enabled collector also serializes one synthetic
//! incident. This measures producer/collector interaction, not real audio or scheduling guarantees.

// The probe uses a subset of the shared production recorder's event variants.
#[allow(dead_code)]
#[path = "../audio_trace.rs"]
mod audio_trace;

use audio_io::Pull;
use audio_trace::{Event, Session, Trace};
use serde_json::{json, Value};
use std::time::{Duration, Instant};

fn stats(values: &mut [u64]) -> Value {
    values.sort_unstable();
    let n = values.len();
    json!({"samples":n,"p50_ns":values[n / 2],"p95_ns":values[(n - 1) * 95 / 100],
        "p99_ns":values[(n - 1) * 99 / 100],"max_ns":values[n - 1]})
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let path = std::env::args()
        .nth(1)
        .ok_or("usage: audio_trace_probe <new-trace.jsonl>")?;
    let session = Session::start(
        std::path::Path::new(&path),
        json!({"source_rate":48000,"mode":"synthetic_producer_overhead_probe"}),
    )?;
    let enabled = session.trace();
    let disabled = Trace::default();
    let mut off = Vec::with_capacity(4096);
    let mut on = Vec::with_capacity(4096);
    let start = Instant::now();
    for i in 0..4096 {
        let event = Event::Fifo {
            pull: if i == 1024 {
                Pull::Underrun
            } else {
                Pull::Block
            },
            fill: 4,
            block: 256,
            cushion: 4,
        };
        let t = Instant::now();
        disabled.record(event);
        off.push(t.elapsed().as_nanos() as u64);
        let t = Instant::now();
        enabled.record(event);
        on.push(t.elapsed().as_nanos() as u64);
        if i % 8 == 0 {
            std::thread::sleep(Duration::from_millis(2));
        }
        if start.elapsed() > Duration::from_secs(20) {
            break;
        }
    }
    session.finish()?;
    let file = std::fs::read_to_string(path)?;
    let footer: Value = serde_json::from_str(file.lines().last().ok_or("missing trace footer")?)?;
    println!(
        "{}",
        json!({"mode":"synthetic_producer_overhead_probe",
        "disabled":stats(&mut off),"enabled":stats(&mut on),"trace_footer":footer})
    );
    if footer["queue_drops"].as_u64() != Some(0) {
        return Err("trace queue dropped observations".into());
    }
    Ok(())
}
