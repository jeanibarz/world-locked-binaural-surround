//! Retain timing context around rare FIFO underruns without writing from audio threads.
//! Producers submit fixed-size observations to a bounded queue. A worker keeps recent
//! history and writes bounded incident windows; evidence describes timing, not causality.

use audio_io::Pull;
use serde_json::{json, Value};
use std::collections::VecDeque;
use std::fs::File;
use std::io::{self, BufWriter, Write};
use std::path::Path;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{mpsc, Arc};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

pub const QUEUE_CAPACITY: usize = 4096;
const HISTORY_CAPACITY: usize = 2048;
const INCIDENT_CAPACITY: usize = 4096;
const MAX_INCIDENTS: usize = 32;
const MAX_FILE_RECORDS: usize = 32768;
const WINDOW_US: u64 = 250_000;
const POST_WINDOW_US: u64 = 1_000_000;

#[derive(Clone, Copy, Debug)]
pub enum Event {
    Capture {
        gap_us: u64,
        read_us: u64,
        frames: usize,
        discontinuity: bool,
        silent: bool,
    },
    Render {
        elapsed_us: u64,
        frames: usize,
        block: usize,
        cushion: usize,
    },
    Output {
        gap_us: u64,
        frames: usize,
        fill: usize,
        block: usize,
        cushion: usize,
        sample_rate: u32,
    },
    Fifo {
        pull: Pull,
        fill: usize,
        block: usize,
        cushion: usize,
    },
    Reconfigure {
        elapsed_us: u64,
        block: usize,
        cushion: usize,
    },
    CaptureError,
}
#[derive(Clone, Copy, Debug)]
struct Stamped {
    at_us: u64,
    event: Event,
}
struct Shared {
    tx: mpsc::SyncSender<Stamped>,
    epoch: Instant,
    drops: AtomicU64,
    stop: AtomicBool,
}
#[derive(Clone, Default)]
pub struct Trace(Option<Arc<Shared>>);
impl Trace {
    pub fn enabled(&self) -> bool {
        self.0
            .as_ref()
            .is_some_and(|shared| !shared.stop.load(Ordering::Relaxed))
    }
    /// Never waits for queue space. A saturated collector loses observations, counted in the footer.
    pub fn record(&self, event: Event) {
        if let Some(shared) = &self.0 {
            if shared.stop.load(Ordering::Relaxed) {
                return;
            }
            let at_us = shared.epoch.elapsed().as_micros().min(u64::MAX as u128) as u64;
            if shared.tx.try_send(Stamped { at_us, event }).is_err() {
                shared.drops.fetch_add(1, Ordering::Relaxed);
            }
        }
    }
}
pub struct Session {
    trace: Trace,
    worker: Option<JoinHandle<io::Result<()>>>,
}
impl Session {
    pub fn start(path: &Path, metadata: Value) -> io::Result<Self> {
        let file = File::create(path)?;
        let recorder = Recorder::new(BufWriter::new(file), metadata)?;
        let (tx, rx) = mpsc::sync_channel(QUEUE_CAPACITY);
        let shared = Arc::new(Shared {
            tx,
            epoch: Instant::now(),
            drops: AtomicU64::new(0),
            stop: AtomicBool::new(false),
        });
        let worker_shared = shared.clone();
        let worker = thread::Builder::new()
            .name("audio-trace".into())
            .spawn(move || {
                let result = (|| -> io::Result<()> {
                    let mut recorder = recorder;
                    loop {
                        match rx.recv_timeout(Duration::from_millis(25)) {
                            Ok(e) => recorder.ingest(e.at_us, e.event)?,
                            Err(mpsc::RecvTimeoutError::Timeout) => {}
                            Err(mpsc::RecvTimeoutError::Disconnected) => break,
                        }
                        if worker_shared.stop.load(Ordering::Acquire) {
                            while let Ok(e) = rx.try_recv() {
                                recorder.ingest(e.at_us, e.event)?;
                            }
                            break;
                        }
                        let mut drained = false;
                        for _ in 0..QUEUE_CAPACITY {
                            match rx.try_recv() {
                                Ok(e) => recorder.ingest(e.at_us, e.event)?,
                                Err(_) => {
                                    drained = true;
                                    break;
                                }
                            }
                        }
                        if drained {
                            recorder.advance(
                                worker_shared
                                    .epoch
                                    .elapsed()
                                    .as_micros()
                                    .min(u64::MAX as u128) as u64,
                            )?;
                        }
                    }
                    recorder.finish(worker_shared.drops.load(Ordering::Relaxed))?;
                    Ok(())
                })();
                if let Err(error) = &result {
                    worker_shared.stop.store(true, Ordering::Release);
                    eprintln!("AUDIO_TRACE_ERROR {error}");
                }
                result
            })?;
        Ok(Self {
            trace: Trace(Some(shared)),
            worker: Some(worker),
        })
    }
    pub fn trace(&self) -> Trace {
        self.trace.clone()
    }
    /// Call after producer threads have stopped, so the footer includes every attempted observation.
    pub fn finish(mut self) -> io::Result<()> {
        self.join()
    }
    fn join(&mut self) -> io::Result<()> {
        if let Some(worker) = self.worker.take() {
            self.trace
                .0
                .as_ref()
                .unwrap()
                .stop
                .store(true, Ordering::Release);
            worker
                .join()
                .map_err(|_| io::Error::other("audio trace worker panicked"))?
        } else {
            Ok(())
        }
    }
}
impl Drop for Session {
    fn drop(&mut self) {
        let _ = self.join();
    }
}

struct Incident {
    trigger: u64,
    events: Vec<Stamped>,
    triggers: usize,
    truncated: bool,
    prehistory_truncated: bool,
}
/// Deterministic collector used by simulations as well as the background worker.
/// Timestamps are microseconds from one session clock; arrival order may differ across producers.
pub struct Recorder<W: Write> {
    writer: W,
    history: VecDeque<Stamped>,
    pending: Option<Incident>,
    source_rate: u64,
    incidents: usize,
    suppressed: u64,
    records: usize,
    truncated: bool,
    history_evictions: u64,
    closed_through: Option<u64>,
    late_events: u64,
}
impl<W: Write> Recorder<W> {
    pub fn new(mut writer: W, metadata: Value) -> io::Result<Self> {
        let source_rate = metadata
            .get("source_rate")
            .and_then(Value::as_u64)
            .unwrap_or(48000)
            .max(1);
        writeln!(
            writer,
            "{}",
            json!({"type":"header","version":1,"metadata":metadata,"pre_window_us":WINDOW_US,"post_window_us":POST_WINDOW_US,"queue_capacity":QUEUE_CAPACITY,"history_capacity":HISTORY_CAPACITY,"incident_capacity":INCIDENT_CAPACITY,"max_incidents":MAX_INCIDENTS,"max_file_records":MAX_FILE_RECORDS,"source_rate":source_rate})
        )?;
        writer.flush()?;
        Ok(Self {
            writer,
            history: VecDeque::with_capacity(HISTORY_CAPACITY),
            pending: None,
            source_rate,
            incidents: 0,
            suppressed: 0,
            records: 0,
            truncated: false,
            history_evictions: 0,
            closed_through: None,
            late_events: 0,
        })
    }
    #[cfg(test)]
    pub fn push(&mut self, at_us: u64, event: Event) -> io::Result<()> {
        self.advance(at_us)?;
        self.ingest(at_us, event)
    }
    fn ingest(&mut self, at_us: u64, event: Event) -> io::Result<()> {
        if self.closed_through.is_some_and(|end| at_us <= end) {
            self.late_events += 1;
        }
        let stamped = Stamped { at_us, event };
        let trigger = matches!(
            event,
            Event::Fifo {
                pull: Pull::Underrun,
                ..
            }
        );
        if trigger {
            self.advance(at_us)?;
        }
        if trigger && self.pending.is_none() {
            if self.incidents < MAX_INCIDENTS && self.records < MAX_FILE_RECORDS {
                let events = self
                    .history
                    .iter()
                    .copied()
                    .filter(|e| {
                        e.at_us >= at_us.saturating_sub(WINDOW_US)
                            && e.at_us <= at_us.saturating_add(POST_WINDOW_US)
                    })
                    .collect();
                self.pending = Some(Incident {
                    trigger: at_us,
                    events,
                    triggers: 0,
                    truncated: false,
                    prehistory_truncated: self.history.len() == HISTORY_CAPACITY,
                });
                self.incidents += 1;
            } else {
                self.suppressed += 1;
            }
        }
        if let Some(pending) = &mut self.pending {
            if at_us >= pending.trigger.saturating_sub(WINDOW_US)
                && at_us <= pending.trigger.saturating_add(POST_WINDOW_US)
            {
                if trigger {
                    pending.triggers += 1;
                }
                if pending.events.len() < INCIDENT_CAPACITY {
                    pending.events.push(stamped);
                } else {
                    pending.truncated = true;
                }
            }
        }
        self.history
            .retain(|e| e.at_us >= at_us.saturating_sub(WINDOW_US));
        if self.history.len() == HISTORY_CAPACITY {
            self.history.pop_front();
            self.history_evictions += 1;
        }
        self.history.push_back(stamped);
        Ok(())
    }
    pub fn advance(&mut self, at_us: u64) -> io::Result<()> {
        if self
            .pending
            .as_ref()
            .is_some_and(|p| at_us > p.trigger.saturating_add(POST_WINDOW_US))
        {
            self.flush_incident(false)?;
        }
        Ok(())
    }
    fn row(&mut self, value: Value) -> io::Result<()> {
        if self.records < MAX_FILE_RECORDS {
            writeln!(self.writer, "{value}")?;
            self.records += 1;
        } else {
            self.truncated = true;
        }
        Ok(())
    }
    fn flush_incident(&mut self, shutdown: bool) -> io::Result<()> {
        let Some(mut incident) = self.pending.take() else {
            return Ok(());
        };
        incident.events.sort_by_key(|e| e.at_us);
        let mut evidence = json!({"capture_gap":false,"capture_discontinuity":false,"capture_error":false,"render_delay":false,"reconfigure_pause":false,"output_gap":false,"output_demand_burst":false});
        let mut overlapping = evidence.clone();
        let margin_us = incident
            .events
            .iter()
            .find_map(|e| match e.event {
                Event::Fifo {
                    pull: Pull::Underrun,
                    block,
                    cushion,
                    ..
                } => Some(
                    (block as u64)
                        .saturating_mul(cushion as u64)
                        .saturating_mul(1_000_000)
                        / self.source_rate,
                ),
                _ => None,
            })
            .unwrap_or(16_000);
        for e in &incident.events {
            let duration = match e.event {
                Event::Capture {
                    gap_us, read_us, ..
                } => gap_us.max(read_us),
                Event::Render { elapsed_us, .. } | Event::Reconfigure { elapsed_us, .. } => {
                    elapsed_us
                }
                Event::Output { gap_us, .. } => gap_us,
                _ => 0,
            };
            let after = e.at_us > incident.trigger;
            if after && e.at_us.saturating_sub(duration) > incident.trigger {
                continue;
            }
            let evidence = if after {
                &mut overlapping
            } else {
                &mut evidence
            };
            match e.event {
                Event::Capture {
                    gap_us,
                    discontinuity,
                    ..
                } => {
                    if gap_us > margin_us {
                        evidence["capture_gap"] = json!(true);
                    }
                    if discontinuity && !after {
                        evidence["capture_discontinuity"] = json!(true);
                    }
                }
                Event::CaptureError => evidence["capture_error"] = json!(true),
                Event::Reconfigure { elapsed_us, .. } => {
                    if elapsed_us > margin_us {
                        evidence["reconfigure_pause"] = json!(true);
                    }
                }
                Event::Render {
                    elapsed_us,
                    frames,
                    block,
                    ..
                } => {
                    if elapsed_us > frames.max(block) as u64 * 1_000_000 / self.source_rate {
                        evidence["render_delay"] = json!(true);
                    }
                }
                Event::Output {
                    gap_us,
                    frames,
                    block,
                    cushion,
                    sample_rate,
                    ..
                } => {
                    let margin = (block as u64).saturating_mul(cushion as u64);
                    if gap_us > margin.saturating_mul(1_000_000) / self.source_rate {
                        evidence["output_gap"] = json!(true);
                    }
                    if !after
                        && frames as u128 * self.source_rate as u128
                            > margin as u128 * sample_rate as u128
                    {
                        evidence["output_demand_burst"] = json!(true);
                    }
                }
                _ => {}
            }
        }
        let id = self.incidents;
        self.row(json!({"type":"incident","incident":id,"trigger_us":incident.trigger,"triggers":incident.triggers,"shutdown_flush":shutdown,"window_truncated":incident.truncated,"prehistory_truncated":incident.prehistory_truncated,"evidence":evidence,"overlapping_duration_evidence":overlapping,"cushion_budget_us":margin_us,"evidence_scope":"evidence: observations_at_or_before_trigger; overlapping_duration_evidence: later_completions_whose_duration_spans_trigger; neither_establishes_causality"}))?;
        for e in incident.events {
            self.row(json!({"type":"event","incident":id,"at_us":e.at_us,"relative_us":e.at_us as i128 - incident.trigger as i128,"observation":event_json(e.event)}))?;
        }
        self.truncated |= incident.truncated;
        self.closed_through = Some(incident.trigger.saturating_add(POST_WINDOW_US));
        self.writer.flush()?;
        Ok(())
    }
    pub fn finish(mut self, queue_drops: u64) -> io::Result<W> {
        self.flush_incident(true)?;
        writeln!(
            self.writer,
            "{}",
            json!({"type":"footer","incidents":self.incidents,"suppressed_triggers":self.suppressed,"queue_drops":queue_drops,"truncated":self.truncated,"history_evictions":self.history_evictions,"late_events_after_window_closed":self.late_events,"file_records":self.records})
        )?;
        self.writer.flush()?;
        Ok(self.writer)
    }
}
fn event_json(e: Event) -> Value {
    match e {
        Event::Capture {
            gap_us,
            read_us,
            frames,
            discontinuity,
            silent,
        } => {
            json!({"stage":"capture","gap_us":gap_us,"read_us":read_us,"frames":frames,"discontinuity":discontinuity,"silent":silent})
        }
        Event::Render {
            elapsed_us,
            frames,
            block,
            cushion,
        } => {
            json!({"stage":"render","elapsed_us":elapsed_us,"frames":frames,"block":block,"cushion":cushion})
        }
        Event::Output {
            gap_us,
            frames,
            fill,
            block,
            cushion,
            sample_rate,
        } => {
            json!({"stage":"output","gap_us":gap_us,"frames":frames,"fill":fill,"block":block,"cushion":cushion,"sample_rate":sample_rate})
        }
        Event::Fifo {
            pull,
            fill,
            block,
            cushion,
        } => {
            json!({"stage":"fifo","pull":match pull { Pull::Block=>"block",Pull::Underrun=>"underrun",Pull::Repriming=>"repriming" },"fill":fill,"block":block,"cushion":cushion})
        }
        Event::Reconfigure {
            elapsed_us,
            block,
            cushion,
        } => json!({"stage":"reconfigure","elapsed_us":elapsed_us,"block":block,"cushion":cushion}),
        Event::CaptureError => json!({"stage":"capture_error"}),
    }
}

#[cfg(test)]
#[path = "audio_trace_tests.rs"]
mod tests;
