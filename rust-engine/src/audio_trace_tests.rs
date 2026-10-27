use super::*;
use std::time::SystemTime;
fn underrun() -> Event {
    Event::Fifo {
        pull: Pull::Underrun,
        fill: 0,
        block: 256,
        cushion: 3,
    }
}
fn capture(gap_us: u64) -> Event {
    Event::Capture {
        gap_us,
        read_us: 100,
        frames: 480,
        discontinuity: false,
        silent: false,
    }
}
fn rows(recorder: Recorder<Vec<u8>>) -> Vec<Value> {
    String::from_utf8(recorder.finish(7).unwrap())
        .unwrap()
        .lines()
        .map(|s| serde_json::from_str(s).unwrap())
        .collect()
}
#[test]
fn pre_and_post_context_shutdown_and_overlapping_triggers() {
    let mut r = Recorder::new(Vec::new(), json!({"source_rate":48000})).unwrap();
    r.push(100_000, capture(10_000)).unwrap();
    r.push(200_000, underrun()).unwrap();
    r.push(210_000, underrun()).unwrap();
    r.push(220_000, capture(90_000)).unwrap();
    let output = rows(r);
    let incident = &output[1];
    assert_eq!(incident["triggers"], 2);
    assert_eq!(incident["shutdown_flush"], true);
    assert_eq!(incident["evidence"]["capture_gap"], false);
    let events: Vec<_> = output.iter().filter(|v| v["type"] == "event").collect();
    assert_eq!(events.len(), 4);
    assert_eq!(events[0]["relative_us"], -100000);
    assert_eq!(events[3]["relative_us"], 20000);
    assert_eq!(output.last().unwrap()["queue_drops"], 7);
}
#[test]
fn concurrent_fault_signals_are_preserved() {
    let mut r = Recorder::new(Vec::new(), json!({"source_rate":48000})).unwrap();
    r.push(
        10,
        Event::Capture {
            gap_us: 50_000,
            read_us: 1,
            frames: 480,
            discontinuity: true,
            silent: false,
        },
    )
    .unwrap();
    r.push(
        11,
        Event::Render {
            elapsed_us: 20_000,
            frames: 256,
            block: 256,
            cushion: 3,
        },
    )
    .unwrap();
    r.push(
        12,
        Event::Output {
            gap_us: 40_000,
            frames: 960,
            fill: 0,
            block: 256,
            cushion: 3,
            sample_rate: 44100,
        },
    )
    .unwrap();
    r.push(13, Event::CaptureError).unwrap();
    r.push(
        13,
        Event::Reconfigure {
            elapsed_us: 50_000,
            block: 256,
            cushion: 3,
        },
    )
    .unwrap();
    r.push(14, underrun()).unwrap();
    let output = rows(r);
    let evidence = output[1]["evidence"].as_object().unwrap();
    assert!(evidence.values().all(|v| v == true));
}
#[test]
fn delayed_completion_reports_overlap_without_rewriting_pretrigger_evidence() {
    let mut r = Recorder::new(Vec::new(), json!({"source_rate":48000})).unwrap();
    r.push(20_000, underrun()).unwrap();
    r.push(
        300_000,
        Event::Render {
            elapsed_us: 300_000,
            frames: 256,
            block: 256,
            cushion: 3,
        },
    )
    .unwrap();
    r.push(
        301_000,
        Event::Capture {
            gap_us: 300_000,
            read_us: 1,
            frames: 480,
            discontinuity: true,
            silent: false,
        },
    )
    .unwrap();
    r.push(
        302_000,
        Event::Output {
            gap_us: 1000,
            frames: 4000,
            fill: 0,
            block: 256,
            cushion: 3,
            sample_rate: 48000,
        },
    )
    .unwrap();
    let output = rows(r);
    assert_eq!(output[1]["evidence"]["render_delay"], false);
    assert_eq!(
        output[1]["overlapping_duration_evidence"]["render_delay"],
        true
    );
    assert_eq!(
        output[1]["overlapping_duration_evidence"]["capture_gap"],
        true
    );
    assert_eq!(
        output[1]["overlapping_duration_evidence"]["capture_discontinuity"],
        false
    );
    assert_eq!(
        output[1]["overlapping_duration_evidence"]["output_demand_burst"],
        false
    );
    assert_eq!(output.iter().filter(|v| v["type"] == "event").count(), 4);
}
#[test]
fn capture_read_duration_is_not_counted_twice_for_overlap() {
    let mut r = Recorder::new(Vec::new(), json!({"source_rate":48000})).unwrap();
    r.push(20_000, underrun()).unwrap();
    // The read ends at 100 ms and started at 50 ms: neither the gap nor
    // the read interval includes the trigger at 20 ms.
    r.push(
        100_000,
        Event::Capture {
            gap_us: 50_000,
            read_us: 50_000,
            frames: 480,
            discontinuity: false,
            silent: false,
        },
    )
    .unwrap();
    let output = rows(r);
    assert_eq!(
        output[1]["overlapping_duration_evidence"]["capture_gap"],
        false
    );
}
#[test]
fn partial_input_has_at_least_one_block_of_render_budget() {
    let mut r = Recorder::new(Vec::new(), json!({"source_rate":48000})).unwrap();
    r.push(
        10_000,
        Event::Render {
            elapsed_us: 100,
            frames: 0,
            block: 256,
            cushion: 3,
        },
    )
    .unwrap();
    r.push(20_000, underrun()).unwrap();
    assert_eq!(rows(r)[1]["evidence"]["render_delay"], false);
}
#[test]
fn reconfiguration_pause_evidence_distinguishes_pretrigger_and_overlap() {
    for (at, pre, overlap) in [
        (10_000, true, false),
        (100_000, false, true),
        (200_000, false, false),
    ] {
        let mut r = Recorder::new(Vec::new(), json!({"source_rate":48000})).unwrap();
        let event = Event::Reconfigure {
            elapsed_us: 100_000,
            block: 256,
            cushion: 3,
        };
        if pre {
            r.push(at, event).unwrap();
        }
        r.push(20_000, underrun()).unwrap();
        if !pre {
            r.push(at, event).unwrap();
        }
        let output = rows(r);
        assert_eq!(output[1]["evidence"]["reconfigure_pause"], pre);
        assert_eq!(
            output[1]["overlapping_duration_evidence"]["reconfigure_pause"],
            overlap
        );
    }
}
#[test]
fn each_known_fault_is_reported_independently() {
    let faults = [
        (capture(50_000), "capture_gap"),
        (
            Event::Capture {
                gap_us: 10_000,
                read_us: 1,
                frames: 480,
                discontinuity: true,
                silent: false,
            },
            "capture_discontinuity",
        ),
        (
            Event::Render {
                elapsed_us: 20_000,
                frames: 256,
                block: 256,
                cushion: 3,
            },
            "render_delay",
        ),
        (
            Event::Output {
                gap_us: 40_000,
                frames: 480,
                fill: 0,
                block: 256,
                cushion: 3,
                sample_rate: 48000,
            },
            "output_gap",
        ),
        (
            Event::Output {
                gap_us: 10_000,
                frames: 960,
                fill: 0,
                block: 256,
                cushion: 3,
                sample_rate: 44100,
            },
            "output_demand_burst",
        ),
    ];
    for (event, key) in faults {
        let mut r = Recorder::new(Vec::new(), json!({"source_rate":48000})).unwrap();
        r.push(1, event).unwrap();
        r.push(2, underrun()).unwrap();
        let output = rows(r);
        let evidence = output[1]["evidence"].as_object().unwrap();
        assert_eq!(evidence[key], true, "{key}");
        assert_eq!(
            evidence.values().filter(|v| **v == true).count(),
            1,
            "{key}"
        );
    }
}
#[test]
fn steady_service_has_no_fault_evidence() {
    let mut r = Recorder::new(Vec::new(), json!({"source_rate":48000})).unwrap();
    r.push(10, capture(10_000)).unwrap();
    r.push(
        11,
        Event::Render {
            elapsed_us: 1000,
            frames: 256,
            block: 256,
            cushion: 3,
        },
    )
    .unwrap();
    r.push(
        12,
        Event::Output {
            gap_us: 10_000,
            frames: 480,
            fill: 3,
            block: 256,
            cushion: 3,
            sample_rate: 48000,
        },
    )
    .unwrap();
    r.push(13, underrun()).unwrap();
    assert!(rows(r)[1]["evidence"]
        .as_object()
        .unwrap()
        .values()
        .all(|v| v == false));
}
#[test]
fn memory_storm_and_file_caps() {
    let mut r = Recorder::new(Vec::new(), json!({})).unwrap();
    for i in 0..20_000 {
        r.push(i, underrun()).unwrap();
    }
    assert_eq!(r.history.len(), HISTORY_CAPACITY);
    assert_eq!(r.pending.as_ref().unwrap().events.len(), INCIDENT_CAPACITY);
    for incident in 1..50 {
        let t = incident * 2_000_000;
        r.push(t, underrun()).unwrap();
        for i in 1..1500 {
            r.push(t + i, capture(10)).unwrap();
        }
    }
    let output = rows(r);
    assert!(output.len() <= MAX_FILE_RECORDS + 2);
    let footer = output.last().unwrap();
    assert_eq!(footer["truncated"], true);
    assert!(footer["suppressed_triggers"].as_u64().unwrap() > 0);
}
#[test]
fn incident_limit_without_file_limit() {
    let mut r = Recorder::new(Vec::new(), json!({})).unwrap();
    for i in 0..40 {
        r.push(i * 2_000_000, underrun()).unwrap();
    }
    let output = rows(r);
    assert_eq!(output.last().unwrap()["incidents"], MAX_INCIDENTS);
    assert_eq!(output.last().unwrap()["suppressed_triggers"], 8);
}
#[test]
fn saturation_is_counted_without_waiting_for_receiver() {
    let (tx, _rx) = mpsc::sync_channel(1);
    let shared = Arc::new(Shared {
        tx,
        epoch: Instant::now(),
        drops: AtomicU64::new(0),
        stop: AtomicBool::new(false),
    });
    let trace = Trace(Some(shared.clone()));
    for _ in 0..100 {
        trace.record(capture(10));
    }
    assert_eq!(shared.drops.load(Ordering::Relaxed), 99);
    assert!(!Trace::default().enabled());
}
#[test]
fn future_arrival_is_not_pretrigger_evidence() {
    let mut r = Recorder::new(Vec::new(), json!({})).unwrap();
    r.push(110, capture(100_000)).unwrap();
    r.push(100, underrun()).unwrap();
    let output = rows(r);
    assert_eq!(output[1]["evidence"]["capture_gap"], false);
    assert_eq!(output[3]["relative_us"], 10);
}
#[test]
fn writer_failures_propagate_from_startup_and_incident_flush() {
    struct FailWriter {
        fail: bool,
    }
    impl Write for FailWriter {
        fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
            if self.fail {
                Err(io::Error::other("injected write failure"))
            } else {
                Ok(buf.len())
            }
        }
        fn flush(&mut self) -> io::Result<()> {
            if self.fail {
                Err(io::Error::other("injected flush failure"))
            } else {
                Ok(())
            }
        }
    }
    assert!(Recorder::new(FailWriter { fail: true }, json!({})).is_err());
    let mut r = Recorder::new(FailWriter { fail: false }, json!({})).unwrap();
    r.push(10, underrun()).unwrap();
    r.writer.fail = true;
    assert_eq!(
        r.finish(0).err().unwrap().to_string(),
        "injected write failure"
    );
}
#[test]
fn stopped_trace_is_disabled() {
    let (tx, _rx) = mpsc::sync_channel(1);
    let shared = Arc::new(Shared {
        tx,
        epoch: Instant::now(),
        drops: AtomicU64::new(0),
        stop: AtomicBool::new(false),
    });
    let trace = Trace(Some(shared.clone()));
    assert!(trace.enabled());
    shared.stop.store(true, Ordering::Release);
    assert!(!trace.enabled());
    trace.record(underrun());
    assert_eq!(shared.drops.load(Ordering::Relaxed), 0);
}
#[test]
fn session_shutdown_joins_and_flushes_pending_incident() {
    let path = std::env::temp_dir().join(format!(
        "wlbs-trace-test-{}-{}.jsonl",
        std::process::id(),
        SystemTime::now()
            .duration_since(SystemTime::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    let session = Session::start(&path, json!({"source_rate":48000})).unwrap();
    assert!(std::fs::read_to_string(&path).unwrap().contains("header"));
    let trace = session.trace();
    trace.record(capture(20_000));
    trace.record(underrun());
    session.finish().unwrap();
    let text = std::fs::read_to_string(&path).unwrap();
    std::fs::remove_file(path).unwrap();
    let output: Vec<Value> = text
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect();
    assert_eq!(output[1]["shutdown_flush"], true);
    assert_eq!(output.last().unwrap()["queue_drops"], 0);
    // A surviving handle must not prevent session shutdown.
    trace.record(underrun());
}
#[test]
#[ignore = "manual producer overhead measurement"]
fn producer_overhead() {
    for enabled in [false, true] {
        let (tx, _rx) = mpsc::sync_channel(QUEUE_CAPACITY);
        let trace = if enabled {
            Trace(Some(Arc::new(Shared {
                tx,
                epoch: Instant::now(),
                drops: AtomicU64::new(0),
                stop: AtomicBool::new(false),
            })))
        } else {
            Trace::default()
        };
        let start = Instant::now();
        for _ in 0..1_000_000 {
            std::hint::black_box(&trace).record(std::hint::black_box(capture(10)));
        }
        eprintln!(
            "trace enabled={enabled} saturated_queue={} ns/record={:.1}",
            enabled,
            start.elapsed().as_nanos() as f64 / 1_000_000.0
        );
    }
}
