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
    /// Where head pose comes from: "camera" | "mjpeg" | "image" | "demo" | "held".
    pub pose_source: &'a str,
    pub tracking: bool,
    pub pose_stale: bool,
    pub cam_res: Option<(u32, u32)>,
    /// A camera/pipeline open failure, shown as "camera unavailable" (BUG-10).
    pub cam_error: Option<&'a str>,
    pub mmcss: bool,
    pub lat: &'a LatencyHistogram,
}

fn block_ms(block: usize, sr: u32) -> f64 {
    if sr == 0 {
        return 0.0;
    }
    block as f64 / sr as f64 * 1000.0
}

/// End-to-end latency estimate (ms): capture-accrue block + render + buffer cushion + device-out
/// block. Shared by the loop (which feeds the histogram) and [`build_state`] so both agree.
pub fn estimate_total_ms(block: usize, sr: u32, cushion_blocks: usize, render_load_pct: f64) -> f64 {
    let bms = block_ms(block, sr);
    let render_ms = render_load_pct / 100.0 * bms;
    let cushion_ms = cushion_blocks as f64 * bms;
    bms + render_ms + cushion_ms + bms
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

    // --- latency breakdown: capture accrue + render + output buffer cushion + device out block ---
    let cushion_ms = t.cushion_blocks as f64 * bms;
    let render_ms = t.render_load_pct / 100.0 * bms;
    let total_ms = estimate_total_ms(t.block, t.sample_rate, t.cushion_blocks, t.render_load_pct);
    let (verdict, vlevel) = if total_ms < 40.0 {
        ("GOOD", "good")
    } else if total_ms < 80.0 {
        ("OK", "warn")
    } else {
        ("HIGH", "bad")
    };
    let segments = json!([
        {"label": "cap", "ms": bms, "capture": true},
        {"label": "render", "ms": render_ms.max(0.1), "bg": "#35d0d0", "fg": "#06222a"},
        {"label": "buffer", "ms": cushion_ms.max(0.1), "bg": "#2b6f78", "fg": "#cdefff"},
        {"label": "out", "ms": bms, "bg": "#4cd98a", "fg": "#06222a"},
    ]);
    let bins = t.lat.heights_pct();
    let p50 = t.lat.p50_pct();

    // --- buffer meter (one cell per FIFO block) ---
    let buffer = json!({
        "text": format!("{} / {} blk", t.fifo_fill, t.fifo_capacity),
        "fill_blocks": t.fifo_fill,
        "cushion_blocks": t.cushion_blocks,
        "capacity": t.fifo_capacity,
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

    json!({
        "title": t.title,
        "cam_res": t.cam_res.map(|(w, h)| format!("{w}×{h}")).unwrap_or_else(|| "—".into()),
        "face_badge": face_badge,
        "face_locked": face_locked,
        "health": health,
        "render_load": render_load,
        "buffer": buffer,
        "dropouts": dropouts,
        "predict": t.predict,
        "invert_tracking": t.invert_tracking,
        "flip_h": t.flip_h,
        "flip_v": t.flip_v,
        "pose": {
            "yaw": format!("{:+.0}°", t.yaw_deg),
            "pitch": "0°",
            "roll": "0°",
            "yaw_deg": t.yaw_deg,
        },
        "facing": facing_label(t.yaw_deg),
        "latency": {
            "ms": format!("{:.0}", total_ms),
            "detail": format!("block {:.1} ms · cushion {} blk", bms, t.cushion_blocks),
            "verdict": verdict,
            "verdict_level": vlevel,
            "segments": segments,
            "bins": bins,
            "p50_pct": p50,
            "axis_lo": t.lat.axis_lo,
            "axis_hi": t.lat.axis_hi,
            "note": format!("end-to-end estimate at block {} @ {} Hz", t.block, t.sample_rate),
        },
    })
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
            pose_source: "camera",
            tracking: true,
            pose_stale: false,
            cam_res: Some((640, 480)),
            cam_error: None,
            mmcss: true,
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
        for key in ["title", "cam_res", "face_badge", "face_locked", "health", "render_load",
                    "buffer", "dropouts", "predict", "invert_tracking", "flip_h", "flip_v",
                    "pose", "facing", "latency"] {
            assert!(s.get(key).is_some(), "missing state field {key}");
        }
        assert_eq!(s["buffer"]["cushion_blocks"], 4);
        assert_eq!(s["buffer"]["capacity"], 8);
        assert!(s["latency"]["segments"].as_array().unwrap().len() == 4);
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
