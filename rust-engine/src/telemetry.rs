//! The cockpit telemetry state object — the single source of truth for what the browser UI renders.
//!
//! The web cockpit (`apps/web_cockpit/cockpit.js::applyState`) reads a rich state object (meters,
//! buffer cells, dropouts, latency breakdown + histogram, health rows, pose/radar). The engine used
//! to push only a handful of raw floats at the wrong nesting, so the UI silently kept its mockup
//! sample data (BUG-02/BUG-06). [`build_state`] builds the full object the UI actually consumes from
//! measured engine inputs, and [`wrap_state`] nests it as the `{"type":"state","state":{…}}` wire
//! frame the UI expects.
//!
//! Pure (no Windows/audio deps) so it is unit-tested on every target, and so the wire contract is
//! pinned by tests rather than only by a live run.

use serde_json::{json, Value};

/// A rolling window of recent end-to-end latency samples (ms), binned into a fixed-width histogram
/// for the cockpit's latency distribution. Also yields the median for the p50 marker.
#[derive(Clone)]
pub struct LatencyHistogram {
    samples: std::collections::VecDeque<f64>,
    cap: usize,
    pub bins: usize,
    pub axis_lo: f64,
    pub axis_hi: f64,
}

impl LatencyHistogram {
    pub fn new(cap: usize, bins: usize, axis_lo: f64, axis_hi: f64) -> Self {
        Self { samples: std::collections::VecDeque::with_capacity(cap), cap, bins, axis_lo, axis_hi }
    }

    /// Record one latency sample (ms).
    pub fn push(&mut self, ms: f64) {
        if self.samples.len() == self.cap {
            self.samples.pop_front();
        }
        self.samples.push_back(ms);
    }

    /// Drop all samples (a block/cushion reconfig changes the latency, so old samples would mix).
    pub fn clear(&mut self) {
        self.samples.clear();
    }

    /// Bin heights normalised to 0..100 (percent of the tallest bin), for the UI's `height: h%` bars.
    pub fn heights_pct(&self) -> Vec<u32> {
        let mut counts = vec![0u32; self.bins];
        if self.samples.is_empty() {
            return counts;
        }
        let span = (self.axis_hi - self.axis_lo).max(1e-9);
        for &s in &self.samples {
            let f = ((s - self.axis_lo) / span).clamp(0.0, 0.999_999);
            let b = (f * self.bins as f64) as usize;
            counts[b.min(self.bins - 1)] += 1;
        }
        let max = counts.iter().copied().max().unwrap_or(1).max(1);
        counts.iter().map(|&c| ((c as f64 / max as f64) * 100.0).round() as u32).collect()
    }

    /// Median latency as a percentage position across the axis (0..100), or `None` if empty.
    pub fn p50_pct(&self) -> Option<f64> {
        if self.samples.is_empty() {
            return None;
        }
        let mut v: Vec<f64> = self.samples.iter().copied().collect();
        v.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
        let med = v[v.len() / 2];
        let span = (self.axis_hi - self.axis_lo).max(1e-9);
        Some((((med - self.axis_lo) / span) * 100.0).clamp(0.0, 100.0))
    }
}

/// Latency-histogram upper axis (ms) for the current estimate: at least 120 ms, else 1.5x the
/// estimate, rounded up to a multiple of 10 so the axis doesn't jitter with every sample.
pub fn hist_axis_hi(total_ms: f64) -> f64 {
    let want = (1.5 * total_ms).max(120.0);
    if want.is_finite() { (want / 10.0).ceil() * 10.0 } else { 120.0 }
}

/// Sliding window over cumulative (underruns, overflow_drops) counters, yielding the delta over the
/// last `span` — the honest "dropouts, last 60 s" figure instead of lifetime totals.
pub struct DropWindow {
    span: std::time::Duration,
    entries: std::collections::VecDeque<(std::time::Instant, u64, u64)>,
}

impl DropWindow {
    pub fn new(span: std::time::Duration) -> Self {
        Self { span, entries: std::collections::VecDeque::new() }
    }

    /// Forget history (the FIFO's counters restart from 0 on a reconfig).
    pub fn clear(&mut self) {
        self.entries.clear();
    }

    /// Record the current cumulative counters; returns (underruns, overflow_drops) within the window.
    pub fn push(&mut self, now: std::time::Instant, underruns: u64, overflow: u64) -> (u64, u64) {
        self.entries.push_back((now, underruns, overflow));
        // Keep the newest entry at-or-before the cutoff as the baseline; drop anything older.
        while self.entries.len() >= 2 && now.duration_since(self.entries[1].0) >= self.span {
            self.entries.pop_front();
        }
        let (_, u0, o0) = self.entries[0];
        (underruns.saturating_sub(u0), overflow.saturating_sub(o0))
    }
}

/// Everything the engine measures each heartbeat, flattened for the state builder.
pub struct TeleInput<'a> {
    pub title: &'a str,
    pub yaw_deg: f64,
    pub predict: bool,
    /// Head-tracking direction inverted (#189) — mirrored to the cockpit so the switch reflects it.
    pub invert_tracking: bool,
    /// Camera un-mirror flips (#191) — mirrored to the cockpit so the switches reflect live state.
    pub flip_h: bool,
    pub flip_v: bool,
    pub block: usize,
    pub sample_rate: u32,
    pub cushion_blocks: usize,
    pub fifo_fill: usize,
    pub fifo_capacity: usize,
    /// Live output-resampler drift correction in ppm (#192) — the PI loop's current nudge holding
    /// the FIFO fill at the cushion. Near-zero and steady = the source/output clocks are reconciled.
    pub resample_ppm: f64,
    pub underruns: u64,
    pub overflow_drops: u64,
    pub discontinuities: u64,
    pub rendered_blocks: u64,
    /// Mean render time per block as a percentage of the block's real-time budget.
    pub render_load_pct: f64,
    pub render_peak_pct: f64,
    pub source_idle: bool,
    /// The most recent capture buffer carried the device "silent" flag (BUG-33).
    pub source_silent: bool,
    pub source_name: &'a str,
    pub source_sr: u32,
    pub source_ch: u16,
    pub output_name: &'a str,
    pub output_sr: u32,
    pub output_err: Option<&'a str>,
    /// User gain applied to the final stereo output, before the safety clamp.
    pub output_gain_db: f64,
    /// Live upper bound on `output_gain_db` (the configurable boost ceiling). Shipped so the
    /// cockpit's gain slider can retune its own `max` live when the ceiling changes.
    pub output_max_gain_db: f64,
    /// Peak and RMS before the safety clamp for this heartbeat; silence has no finite dBFS value.
    pub output_peak_dbfs: Option<f64>,
    pub output_rms_dbfs: Option<f64>,
    /// Sample values exceeding full scale during this heartbeat (each stereo channel counts).
    pub output_clipped_samples: u64,
    pub output_clipped_total: u64,
    pub output_non_finite_total: u64,
    /// Where head pose comes from: "camera" | "mjpeg" | "image" | "demo" | "held".
    pub pose_source: &'a str,
    pub tracking: bool,
    pub pose_stale: bool,
    /// Smoothed inter-pose interval (ms) of the live camera tracker, or 0 when pose comes from a
    /// non-camera source (demo/held) or no two poses exist yet. Drives the head-tracking latency
    /// stage (`1.5 ×` it — see [`tracking_stage_ms`]), the camera half of the end-to-end estimate
    /// that the legacy Python cockpit showed and this engine previously omitted.
    pub pose_interval_ms: f64,
    pub cam_res: Option<(u32, u32)>,
    /// A camera/pipeline open failure, shown as "camera unavailable" (BUG-10).
    pub cam_error: Option<&'a str>,
    pub mmcss: bool,
    /// Draw-landmarks setting (#185) — mirrored to the cockpit so the settings switch reflects the
    /// live/persisted value, exactly like `predict`/`flip_h`.
    pub show_landmarks: bool,
    /// FaceMesh landmark overlay for the camera preview (#185): interleaved `[x0,y0,x1,y1,…]` in
    /// per-mille (0..1000) of the **un-mirrored** frame's width/height. `None` when there is no face,
    /// tracking is stale, or the overlay is switched off — the cockpit then clears the overlay.
    /// Captured on the vision thread at the pose rate and carried here at the telemetry cadence,
    /// never per audio block.
    pub landmarks: Option<&'a [u16]>,
    /// Face bounding box for the overlay (#185): `[x, y, w, h]` in per-mille of the frame, derived
    /// from the landmark extent. Present exactly when `landmarks` is.
    pub face_box: Option<[u16; 4]>,
    pub lat: &'a LatencyHistogram,
}

fn block_ms(block: usize, sr: u32) -> f64 {
    if sr == 0 {
        return 0.0;
    }
    block as f64 / sr as f64 * 1000.0
}

/// Head-tracking (camera) stage of the end-to-end "I turn my head → I hear it move" delay, in ms.
///
/// The camera only reports a *new* pose every `pose_interval_ms`, and a frame is already ~one
/// interval old by the time it is decoded and solved, so the pose carried into the render is
/// ~1.5 intervals old on average. This reproduces the legacy Python model exactly
/// (`binaural_headtracker.diagnostics.classify`: `tracking_ms = 1.5 * pose_interval_ms`), where the
/// unmeasurable sensor exposure/transport is folded into the same estimate. `pose_interval_ms <= 0`
/// (no live camera tracker — a demo/held yaw, or before the first two poses) ⇒ 0.
///
/// This is the single largest and most world-lock-relevant component of the head→ear delay, and it
/// was previously **absent** from the Rust estimate (which summed only the audio path), so the
/// cockpit under-reported the true latency by a whole tracking stage (~100 ms at the default
/// stride-2 / 30 fps ≈ 15 Hz pose rate).
pub fn tracking_stage_ms(pose_interval_ms: f64) -> f64 {
    if pose_interval_ms > 0.0 {
        1.5 * pose_interval_ms
    } else {
        0.0
    }
}

/// End-to-end latency estimate (ms): head-tracking (camera) stage + capture-accrue block + render +
/// buffer cushion + device-out block. Shared by the loop (which feeds the histogram) and
/// [`build_state`] so both agree. `pose_interval_ms` is the measured inter-pose interval (0 when
/// there is no live camera tracker — then the tracking stage is 0 and this is the pure audio path).
pub fn estimate_total_ms(
    block: usize,
    sr: u32,
    cushion_blocks: usize,
    render_load_pct: f64,
    pose_interval_ms: f64,
) -> f64 {
    let bms = block_ms(block, sr);
    let render_ms = render_load_pct / 100.0 * bms;
    let cushion_ms = cushion_blocks as f64 * bms;
    tracking_stage_ms(pose_interval_ms) + bms + render_ms + cushion_ms + bms
}

/// A short compass-ish label for the current head yaw (+ = turned left, per `geometry`).
fn facing_label(yaw_deg: f64) -> String {
    let y = yaw_deg;
    if y.abs() < 7.0 {
        "Facing forward".into()
    } else if y > 0.0 {
        format!("Turned left {:.0}°", y.abs())
    } else {
        format!("Turned right {:.0}°", y.abs())
    }
}

/// Build the full cockpit state object from measured inputs. Every field maps to a branch of
/// `cockpit.js::applyState`; the UI guards each one, so a partial object degrades gracefully.
pub fn build_state(t: &TeleInput) -> Value {
    let bms = block_ms(t.block, t.sample_rate);

    // --- latency breakdown: head-tracking (camera) + capture accrue + render + output buffer
    // cushion + device out block. The tracking stage is an estimate (1.5 × the measured pose
    // interval), shown with the same muted/hatched "estimated" treatment as the capture block. ---
    let cushion_ms = t.cushion_blocks as f64 * bms;
    let render_ms = t.render_load_pct / 100.0 * bms;
    let track_ms = tracking_stage_ms(t.pose_interval_ms);
    let total_ms =
        estimate_total_ms(t.block, t.sample_rate, t.cushion_blocks, t.render_load_pct, t.pose_interval_ms);
    // Thresholds are for the whole head→ear delay, so they account for the tracking floor: a brisk
    // tracker + tight audio lands GOOD, the default ~15 Hz tracker (~100 ms tracking alone) lands
    // OK, and a slow tracker or a big cushion lands HIGH. (Before the tracking stage was included
    // the total was audio-only and these read 40/80; raised so the verdict still means something.)
    let (verdict, vlevel) = if total_ms < 90.0 {
        ("GOOD", "good")
    } else if total_ms < 150.0 {
        ("OK", "warn")
    } else {
        ("HIGH", "bad")
    };
    let mut segments = vec![
        json!({"label": "cap", "ms": bms, "capture": true}),
        json!({"label": "render", "ms": render_ms.max(0.1), "bg": "#35d0d0", "fg": "#06222a"}),
        json!({"label": "buffer", "ms": cushion_ms.max(0.1), "bg": "#2b6f78", "fg": "#cdefff"}),
        json!({"label": "out", "ms": bms, "bg": "#4cd98a", "fg": "#06222a"}),
    ];
    // Prepend the head-tracking stage only when a live camera tracker is driving the pose (so a
    // demo/held-yaw run keeps the pure audio breakdown instead of an empty "track" box).
    if track_ms > 0.0 {
        segments.insert(0, json!({"label": "track", "ms": track_ms, "capture": true}));
    }
    let segments = Value::Array(segments);
    let bins = t.lat.heights_pct();
    let p50 = t.lat.p50_pct();

    // --- buffer meter (one cell per FIFO block) ---
    let buffer = json!({
        "text": format!("{} / {} blk", t.fifo_fill, t.fifo_capacity),
        "fill_blocks": t.fifo_fill,
        "cushion_blocks": t.cushion_blocks,
        "capacity": t.fifo_capacity,
        "resample_ppm": t.resample_ppm,
    });

    // --- dropouts: underruns dominate; overflow drops are the drift cap firing ---
    let drops = t.underruns + t.overflow_drops;
    let (dcolor, dpct) = if drops == 0 {
        ("#4cd98a", 0.0)
    } else if t.underruns == 0 {
        ("#f0c04c", 40.0)
    } else {
        ("#f2545b", (20.0 + drops as f64).min(100.0))
    };
    let dropouts = json!({
        "text": if drops == 0 { "none".to_string() } else { format!("{drops} ({} underrun)", t.underruns) },
        "color": dcolor,
        "pct": dpct,
    });

    // --- render load meter ---
    let rlevel = if t.render_load_pct < 50.0 {
        "good"
    } else if t.render_load_pct < 85.0 {
        "warn"
    } else {
        "bad"
    };
    let render_load = json!({
        "text": format!("{:.0}%", t.render_load_pct),
        "pct": t.render_load_pct.clamp(0.0, 100.0),
        "level": rlevel,
        "peak_pct": t.render_peak_pct.clamp(0.0, 100.0),
    });

    // --- health rows ---
    let green = "#4cd98a";
    let amber = "#f0c04c";
    let red = "#f2545b";
    let mut health = vec![
        json!({
            "dot": if t.source_idle { amber } else { green },
            "label": "Source",
            "value": format!("{} · {} Hz · {}ch{}", t.source_name, t.source_sr, t.source_ch,
                             if t.source_idle { " · idle" } else if t.source_silent { " · silent" } else { "" }),
        }),
        json!({
            "dot": if t.output_err.is_some() { red } else { green },
            "label": "Output",
            "value": match t.output_err {
                Some(e) => format!("error: {e}"),
                None => format!("{} · {} Hz", t.output_name, t.output_sr),
            },
        }),
    ];
    let pose_dot = if t.cam_error.is_some() {
        red
    } else if !t.tracking {
        amber
    } else if t.pose_stale {
        red
    } else {
        green
    };
    let pose_val = if let Some(e) = t.cam_error {
        format!("camera unavailable — {e}")
    } else {
        match (t.pose_source, t.cam_res) {
            ("camera", Some((w, h))) => format!("camera {w}×{h}{}", if t.pose_stale { " · stale" } else if t.tracking { " · locked" } else { " · searching" }),
            ("held", _) => "no tracker (held yaw 0)".to_string(),
            (src, _) => format!("{src}{}", if t.pose_stale { " · stale" } else if t.tracking { " · locked" } else { " · searching" }),
        }
    };
    health.push(json!({"dot": pose_dot, "label": "Head pose", "value": pose_val}));
    health.push(json!({
        "dot": if t.discontinuities > 0 { amber } else { green },
        "label": "Engine",
        "value": format!("rust · {} blocks{}{}", t.rendered_blocks,
                         if t.mmcss { " · pro-audio" } else { "" },
                         if t.discontinuities > 0 { format!(" · {} glitches", t.discontinuities) } else { String::new() }),
    }));

    // --- camera badge ---
    let face_locked = t.tracking && !t.pose_stale && t.cam_error.is_none();
    let face_badge = if t.cam_error.is_some() {
        "camera unavailable"
    } else if t.pose_source == "held" {
        "no camera"
    } else if !t.tracking {
        "searching…"
    } else if t.pose_stale {
        "signal lost"
    } else {
        "tracking"
    };

    let mut out = json!({
        "title": t.title,
        "cam_res": t.cam_res.map(|(w, h)| format!("{w}×{h}")).unwrap_or_else(|| "—".into()),
        "face_badge": face_badge,
        "face_locked": face_locked,
        "show_landmarks": t.show_landmarks,
        "health": health,
        "render_load": render_load,
        "output_level": {
            "gain_db": t.output_gain_db,
            "max_gain_db": t.output_max_gain_db,
            "peak_dbfs": t.output_peak_dbfs.filter(|v| v.is_finite()),
            "rms_dbfs": t.output_rms_dbfs.filter(|v| v.is_finite()),
            "clipped_samples": t.output_clipped_samples,
            "clipped_total": t.output_clipped_total,
            "non_finite_total": t.output_non_finite_total,
        },
        "buffer": buffer,
        "dropouts": dropouts,
        "predict": t.predict,
        "invert_tracking": t.invert_tracking,
        "flip_h": t.flip_h,
        "flip_v": t.flip_v,
        // Only yaw is estimated from the tracker; pitch/roll are not solved, so they are omitted
        // rather than reported as a hard-coded "0°" (the cockpit hides the PITCH/ROLL tiles — #212).
        "pose": {
            "yaw": format!("{:+.0}°", t.yaw_deg),
            "yaw_deg": t.yaw_deg,
        },
        "facing": facing_label(t.yaw_deg),
        "latency": {
            "ms": format!("{:.0}", total_ms),
            "detail": if track_ms > 0.0 {
                format!("track {:.0} ms · block {:.1} ms · cushion {} blk", track_ms, bms, t.cushion_blocks)
            } else {
                format!("block {:.1} ms · cushion {} blk", bms, t.cushion_blocks)
            },
            "verdict": verdict,
            "verdict_level": vlevel,
            "segments": segments,
            "bins": bins,
            "p50_pct": p50,
            "axis_lo": t.lat.axis_lo,
            "axis_hi": t.lat.axis_hi,
            "note": if track_ms > 0.0 {
                format!("head→ear estimate: {:.0} ms tracking (1.5 × {:.0} ms pose interval) + audio, block {} @ {} Hz",
                        track_ms, t.pose_interval_ms, t.block, t.sample_rate)
            } else {
                format!("end-to-end estimate at block {} @ {} Hz", t.block, t.sample_rate)
            },
        },
    });

    // FaceMesh overlay (#185): only present when the engine decided to show it (face present,
    // tracking fresh, toggle on) — the UI clears the overlay whenever the arrays are absent. Emitted
    // as compact per-mille integer arrays rather than floats to keep the 20 Hz state frame small.
    if let (Value::Object(map), Some(pts)) = (&mut out, t.landmarks) {
        map.insert("landmarks".into(), json!(pts));
        if let Some(b) = t.face_box {
            map.insert("face_box".into(), json!(b));
        }
    }
    out
}

/// Nest a state object as the `{"type":"state","state":{…}}` wire frame the cockpit UI expects.
pub fn wrap_state(state: Value) -> String {
    json!({"type": "state", "state": state}).to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_input<'a>(lat: &'a LatencyHistogram) -> TeleInput<'a> {
        TeleInput {
            title: "wlbs",
            yaw_deg: 12.0,
            predict: true,
            invert_tracking: false,
            flip_h: true,
            flip_v: false,
            block: 256,
            sample_rate: 48_000,
            cushion_blocks: 4,
            fifo_fill: 4,
            fifo_capacity: 8,
            resample_ppm: 0.0,
            underruns: 0,
            overflow_drops: 0,
            discontinuities: 0,
            rendered_blocks: 1000,
            render_load_pct: 22.0,
            render_peak_pct: 38.0,
            source_idle: false,
            source_silent: false,
            source_name: "VoiceMeeter",
            source_sr: 48_000,
            source_ch: 8,
            output_name: "Headphones",
            output_sr: 48_000,
            output_err: None,
            output_gain_db: 0.0,
            output_max_gain_db: 24.0,
            output_peak_dbfs: None,
            output_rms_dbfs: None,
            output_clipped_samples: 0,
            output_clipped_total: 0,
            output_non_finite_total: 0,
            pose_source: "camera",
            tracking: true,
            pose_stale: false,
            pose_interval_ms: 66.0,
            cam_res: Some((640, 480)),
            cam_error: None,
            mmcss: true,
            show_landmarks: true,
            landmarks: None,
            face_box: None,
            lat,
        }
    }

    #[test]
    fn wire_frame_nests_under_state() {
        let lat = LatencyHistogram::new(64, 20, 0.0, 120.0);
        let s = build_state(&sample_input(&lat));
        let frame: Value = serde_json::from_str(&wrap_state(s)).unwrap();
        assert_eq!(frame["type"], "state");
        assert!(frame["state"].is_object());
        assert_eq!(frame["state"]["pose"]["yaw_deg"], 12.0);
    }

    #[test]
    fn builds_every_field_the_ui_reads() {
        let lat = LatencyHistogram::new(64, 20, 0.0, 120.0);
        let s = build_state(&sample_input(&lat));
        for key in ["title", "cam_res", "face_badge", "face_locked", "show_landmarks", "health",
                    "render_load", "buffer", "dropouts", "predict", "invert_tracking", "flip_h",
                    "flip_v", "pose", "facing", "latency", "output_level"] {
            assert!(s.get(key).is_some(), "missing state field {key}");
        }
        assert_eq!(s["buffer"]["cushion_blocks"], 4);
        assert_eq!(s["buffer"]["capacity"], 8);
        assert!(s["buffer"]["resample_ppm"].is_number());
        // camera sample ⇒ 5 segments, the head-tracking stage prepended to the 4 audio stages.
        assert_eq!(s["latency"]["segments"].as_array().unwrap().len(), 5);
        assert_eq!(s["latency"]["segments"][0]["label"], "track");
        assert_eq!(s["latency"]["segments"][0]["capture"], true);
    }

    #[test]
    fn pose_reports_only_yaw_not_hardcoded_pitch_roll() {
        // #212: pitch/roll are not estimated, so the cockpit hides those tiles and build_state must
        // not emit placeholder "0°" values that imply tracking that does not exist.
        let lat = LatencyHistogram::new(64, 20, 0.0, 120.0);
        let s = build_state(&sample_input(&lat));
        assert_eq!(s["pose"]["yaw"], "+12°");
        assert_eq!(s["pose"]["yaw_deg"], 12.0);
        assert!(s["pose"].get("pitch").is_none(), "pitch must be omitted, not hard-coded");
        assert!(s["pose"].get("roll").is_none(), "roll must be omitted, not hard-coded");
    }

    #[test]
    fn tracking_stage_is_one_and_a_half_pose_intervals() {
        // Matches the legacy Python model (diagnostics.classify: tracking_ms = 1.5 * pose_interval).
        assert_eq!(tracking_stage_ms(66.0), 99.0);
        assert_eq!(tracking_stage_ms(0.0), 0.0); // no tracker yet ⇒ no stage
        assert_eq!(tracking_stage_ms(-5.0), 0.0); // guarded

        // The tracking stage must land in the end-to-end total, not just the breakdown.
        let audio_only = estimate_total_ms(256, 48_000, 4, 22.0, 0.0);
        let with_track = estimate_total_ms(256, 48_000, 4, 22.0, 66.0);
        assert!((with_track - audio_only - 99.0).abs() < 1e-9);
    }

    #[test]
    fn non_camera_source_shows_audio_only_breakdown() {
        // A demo/held yaw has no pose interval ⇒ no "track" segment, the original 4-stage bar, and
        // a total with no tracking stage (so the fix never inflates latency when there's no camera).
        let lat = LatencyHistogram::new(64, 20, 0.0, 120.0);
        let mut input = sample_input(&lat);
        input.pose_source = "demo";
        input.pose_interval_ms = 0.0;
        let s = build_state(&input);
        let segs = s["latency"]["segments"].as_array().unwrap();
        assert_eq!(segs.len(), 4);
        assert!(segs.iter().all(|g| g["label"] != "track"));
    }

    #[test]
    fn output_levels_preserve_silence_as_null() {
        let lat = LatencyHistogram::new(64, 20, 0.0, 120.0);
        let state = build_state(&sample_input(&lat));
        assert_eq!(state["output_level"], json!({
            "gain_db": 0.0, "max_gain_db": 24.0, "peak_dbfs": null, "rms_dbfs": null,
            "clipped_samples": 0, "clipped_total": 0, "non_finite_total": 0,
        }));
    }

    #[test]
    fn output_levels_keep_overshoot_and_distinct_interval_and_lifetime_counts() {
        let lat = LatencyHistogram::new(64, 20, 0.0, 120.0);
        let mut input = sample_input(&lat);
        input.output_gain_db = 12.0;
        input.output_max_gain_db = 36.0; // a raised ceiling is carried through to the wire frame
        input.output_peak_dbfs = Some(6.0);
        input.output_rms_dbfs = Some(-3.25);
        input.output_clipped_samples = 17;
        input.output_clipped_total = 1017;
        input.output_non_finite_total = 2;
        let frame: Value = serde_json::from_str(&wrap_state(build_state(&input))).unwrap();
        assert_eq!(frame["state"]["output_level"], json!({
            "gain_db": 12.0, "max_gain_db": 36.0, "peak_dbfs": 6.0, "rms_dbfs": -3.25,
            "clipped_samples": 17, "clipped_total": 1017, "non_finite_total": 2,
        }));
        input.output_clipped_samples = 0;
        let next = build_state(&input);
        assert_eq!(next["output_level"]["clipped_samples"], 0);
        assert_eq!(next["output_level"]["clipped_total"], 1017);
    }

    #[test]
    fn output_levels_replace_non_finite_measurements_with_null() {
        let lat = LatencyHistogram::new(64, 20, 0.0, 120.0);
        let mut input = sample_input(&lat);
        for invalid in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            input.output_peak_dbfs = Some(invalid);
            input.output_rms_dbfs = Some(invalid);
            let state = build_state(&input);
            assert!(state["output_level"]["peak_dbfs"].is_null());
            assert!(state["output_level"]["rms_dbfs"].is_null());
            assert!(serde_json::from_str::<Value>(&wrap_state(state)).is_ok());
        }
    }

    #[test]
    fn landmarks_present_only_when_supplied() {
        let lat = LatencyHistogram::new(64, 20, 0.0, 120.0);
        // Absent by default (no face / stale / toggle off): the overlay arrays are omitted, but the
        // toggle-reflection flag is always present.
        let s = build_state(&sample_input(&lat));
        assert_eq!(s["show_landmarks"], true);
        assert!(s.get("landmarks").is_none());
        assert!(s.get("face_box").is_none());

        // Supplied: the flat per-mille array and the box are carried verbatim for the canvas overlay.
        let pts: [u16; 6] = [100, 200, 300, 400, 500, 600];
        let mut inp = sample_input(&lat);
        inp.landmarks = Some(&pts);
        inp.face_box = Some([90, 190, 420, 420]);
        let s = build_state(&inp);
        assert_eq!(s["landmarks"], json!([100, 200, 300, 400, 500, 600]));
        assert_eq!(s["face_box"], json!([90, 190, 420, 420]));
    }

    #[test]
    fn dropouts_flip_red_on_underrun() {
        let lat = LatencyHistogram::new(64, 20, 0.0, 120.0);
        let mut inp = sample_input(&lat);
        inp.underruns = 3;
        let s = build_state(&inp);
        assert_eq!(s["dropouts"]["color"], "#f2545b");
    }

    #[test]
    fn axis_hi_floor_and_scale() {
        assert_eq!(hist_axis_hi(30.0), 120.0);
        assert_eq!(hist_axis_hi(170.0), 260.0); // 255 -> 260
        assert_eq!(hist_axis_hi(f64::NAN), 120.0);
    }

    #[test]
    fn drop_window_reports_delta_over_span() {
        use std::time::{Duration, Instant};
        let t0 = Instant::now();
        let mut w = DropWindow::new(Duration::from_secs(60));
        assert_eq!(w.push(t0, 5, 1), (0, 0)); // lifetime counts before the first sample don't count
        assert_eq!(w.push(t0 + Duration::from_secs(30), 7, 1), (2, 0));
        assert_eq!(w.push(t0 + Duration::from_secs(70), 8, 3), (3, 2)); // baseline t0 is still the newest entry at-or-before the 60 s cutoff
        assert_eq!(w.push(t0 + Duration::from_secs(200), 8, 3), (0, 0));
        w.clear();
        assert_eq!(w.push(t0 + Duration::from_secs(201), 0, 0), (0, 0)); // counters restarted
    }

    #[test]
    fn histogram_bins_and_median() {
        let mut lat = LatencyHistogram::new(64, 10, 0.0, 100.0);
        for _ in 0..10 {
            lat.push(50.0);
        }
        let h = lat.heights_pct();
        assert_eq!(h.len(), 10);
        assert_eq!(h[5], 100); // all samples land in the middle bin → tallest
        let p50 = lat.p50_pct().unwrap();
        assert!((p50 - 50.0).abs() < 1.0);
    }
}
