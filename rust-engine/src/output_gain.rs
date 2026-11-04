//! Final stereo output gain, with a short shared-ear ramp and pre-clamp level meters.
//!
//! Meter levels describe the requested signal before the existing safety clamp. They can exceed
//! 0 dBFS; clipped samples count values that actually exceed full scale, not values equal to it.

#[derive(Debug, Clone, Copy, Default)]
pub struct Metrics {
    pub peak_dbfs: Option<f64>,
    pub rms_dbfs: Option<f64>,
    pub clipped_samples: u64,
    pub non_finite_samples: u64,
    pub samples: u64,
    pub clipped_total: u64,
    pub non_finite_total: u64,
}

/// Fixed attenuation floor for the output trim. Only the positive boost headroom is
/// user-configurable (see [`sanitize_max_gain_db`]) — attenuation is always safe, so the floor
/// never needs to move; a louder-than-usual source is what needs more room to be lifted.
pub const GAIN_FLOOR_DB: f64 = -24.0;

/// Default boost ceiling — the historical fixed maximum, kept as the out-of-the-box value so an
/// unconfigured install behaves exactly as before.
pub const DEFAULT_MAX_GAIN_DB: f64 = 24.0;

/// The configurable boost ceiling is itself bounded. It is never allowed below 0 dB (a ceiling
/// under unity would forbid any boost, and a ceiling under the floor would be an empty range) and
/// never above +60 dB (1000×) — a hearing-safety and finite-overflow guard set well past any
/// legitimate make-up gain.
pub const MIN_MAX_GAIN_DB: f64 = 0.0;
pub const MAX_MAX_GAIN_DB: f64 = 60.0;

/// Clamp a requested boost ceiling into the allowed span. A non-finite request falls back to the
/// default rather than propagating NaN into the trim range (NaN comparisons would reject every
/// gain, silencing the trim).
pub fn sanitize_max_gain_db(max_gain_db: f64) -> f64 {
    if !max_gain_db.is_finite() {
        return DEFAULT_MAX_GAIN_DB;
    }
    max_gain_db.clamp(MIN_MAX_GAIN_DB, MAX_MAX_GAIN_DB)
}

/// Accepted output trim range for a given boost ceiling, shared by persistence and request
/// validation: finite and within `[GAIN_FLOOR_DB, max_gain_db]`.
pub fn valid_gain_db(gain_db: f64, max_gain_db: f64) -> bool {
    gain_db.is_finite() && (GAIN_FLOOR_DB..=max_gain_db).contains(&gain_db)
}

/// Clip a gain to the trim range for `max_gain_db`: a value above the ceiling comes down to it, a
/// value below the floor comes up to it, everything in between is left unchanged. Used when the
/// ceiling moves so a now-too-loud gain follows it down while a lower setting is untouched. The
/// ceiling is sanitised first, so the range is always non-empty and the clamp never panics.
pub fn clamp_gain_db(gain_db: f64, max_gain_db: f64) -> f64 {
    gain_db.clamp(GAIN_FLOOR_DB, sanitize_max_gain_db(max_gain_db))
}

pub struct GainStage {
    gain_db: f64,
    /// Upper bound on `gain_db`, configurable at runtime (see [`GainStage::set_max_gain_db`]).
    max_gain_db: f64,
    current: f64,
    target: f64,
    step: f64,
    remaining: usize,
    ramp_frames: usize,
    peak: f64,
    square_scale: f64,
    square_sum: f64,
    metrics: Metrics,
}

impl GainStage {
    pub fn new(sample_rate: u32) -> Self {
        Self {
            gain_db: 0.0,
            max_gain_db: DEFAULT_MAX_GAIN_DB,
            current: 1.0,
            target: 1.0,
            step: 0.0,
            remaining: 0,
            ramp_frames: ((sample_rate as usize + 50) / 100).max(1),
            peak: 0.0,
            square_scale: 0.0,
            square_sum: 0.0,
            metrics: Metrics::default(),
        }
    }

    /// Reject non-finite or out-of-range settings without changing the current ramp. "Out of
    /// range" is relative to the live boost ceiling (see [`GainStage::set_max_gain_db`]).
    pub fn set_gain_db(&mut self, gain_db: f64) -> bool {
        if !valid_gain_db(gain_db, self.max_gain_db) {
            return false;
        }
        if gain_db == self.gain_db {
            return true;
        }
        self.gain_db = gain_db;
        self.target = 10.0_f64.powf(gain_db / 20.0);
        self.remaining = self.ramp_frames;
        self.step = (self.target - self.current) / self.remaining as f64;
        true
    }

    pub fn gain_db(&self) -> f64 {
        self.gain_db
    }

    /// The live boost ceiling (see [`GainStage::set_max_gain_db`]).
    pub fn max_gain_db(&self) -> f64 {
        self.max_gain_db
    }

    /// Raise or lower the boost ceiling. The request is sanitised to the allowed span first (see
    /// [`sanitize_max_gain_db`]). When the current gain now exceeds the new ceiling it is clipped
    /// down to it — ramping like any other change, so the level drops smoothly rather than
    /// stepping — while a gain already within range is left exactly as it was. Returns the
    /// resulting gain in dB so a caller holding its own copy (e.g. the persisted setting) can
    /// follow the clip.
    pub fn set_max_gain_db(&mut self, max_gain_db: f64) -> f64 {
        self.max_gain_db = sanitize_max_gain_db(max_gain_db);
        if self.gain_db > self.max_gain_db {
            self.set_gain_db(self.max_gain_db);
        }
        self.gain_db
    }

    /// Replace `output` with interleaved stereo. Reserve its capacity before the audio loop.
    /// Each pair gets the same gain, even during a ramp. Engine blocks contain whole frames.
    pub fn process_into(&mut self, input: &[f64], output: &mut Vec<f32>) {
        output.clear();
        for frame in input.chunks(2) {
            if self.remaining > 0 {
                self.remaining -= 1;
                self.current = if self.remaining == 0 {
                    self.target
                } else {
                    self.current + self.step
                };
            }
            for &sample in frame {
                self.metrics.samples = self.metrics.samples.saturating_add(1);
                let value = if sample.is_finite() {
                    let gained = sample * self.current;
                    // A finite extreme input may overflow during gain. Preserve its clipping
                    // polarity and a finite meter value rather than feeding infinity onward.
                    if gained.is_finite() {
                        gained
                    } else {
                        f64::MAX.copysign(sample)
                    }
                } else {
                    self.metrics.non_finite_samples =
                        self.metrics.non_finite_samples.saturating_add(1);
                    self.metrics.non_finite_total = self.metrics.non_finite_total.saturating_add(1);
                    0.0
                };
                let magnitude = value.abs();
                self.peak = self.peak.max(magnitude);
                if magnitude > 0.0 {
                    // Scaled sum of squares avoids overflow for finite but extreme samples.
                    if magnitude > self.square_scale {
                        self.square_sum =
                            1.0 + self.square_sum * (self.square_scale / magnitude).powi(2);
                        self.square_scale = magnitude;
                    } else {
                        self.square_sum += (magnitude / self.square_scale).powi(2);
                    }
                }
                if magnitude > 1.0 {
                    self.metrics.clipped_samples = self.metrics.clipped_samples.saturating_add(1);
                    self.metrics.clipped_total = self.metrics.clipped_total.saturating_add(1);
                }
                output.push(value.clamp(-1.0, 1.0) as f32);
            }
        }
    }

    /// Drain interval meters while retaining lifetime counters and the gain ramp.
    /// Silence and empty intervals have no finite dBFS level and return `None`.
    pub fn take_metrics(&mut self) -> Metrics {
        let mut result = self.metrics;
        result.peak_dbfs = (self.peak > 0.0).then(|| 20.0 * self.peak.log10());
        result.rms_dbfs = (self.square_scale > 0.0 && result.samples > 0).then(|| {
            20.0 * self.square_scale.log10()
                + 10.0 * (self.square_sum / result.samples as f64).log10()
        });
        self.metrics = Metrics {
            clipped_total: result.clipped_total,
            non_finite_total: result.non_finite_total,
            ..Metrics::default()
        };
        self.peak = 0.0;
        self.square_scale = 0.0;
        self.square_sum = 0.0;
        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unity_matches_legacy_conversion_and_preserves_capacity() {
        let mut stage = GainStage::new(48000);
        let input = [-2.0, -1.0, -0.123456789, -0.0, 0.0, 0.123456789, 1.0, 2.0];
        let mut output = Vec::with_capacity(input.len());
        let ptr = output.as_ptr();
        stage.process_into(&input, &mut output);
        assert_eq!(ptr, output.as_ptr());
        for (&actual, &sample) in output.iter().zip(&input) {
            assert_eq!(actual.to_bits(), (sample.clamp(-1.0, 1.0) as f32).to_bits());
        }
        assert_eq!(stage.take_metrics().clipped_samples, 2);
    }

    #[test]
    fn settings_validate_without_disturbing_gain() {
        let mut stage = GainStage::new(48000);
        assert!(stage.set_gain_db(24.0));
        for invalid in [24.01, -24.01, f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            assert!(!stage.set_gain_db(invalid));
            assert_eq!(stage.gain_db(), 24.0);
        }
        assert!(stage.set_gain_db(-24.0));
    }

    #[test]
    fn ramp_is_stereo_shared_and_independent_of_block_partition() {
        let input = vec![0.01; 1920];
        let mut a = GainStage::new(48000);
        let mut b = GainStage::new(48000);
        a.set_gain_db(12.0);
        b.set_gain_db(12.0);
        let mut whole = Vec::new();
        let mut partial = Vec::new();
        let mut joined = Vec::new();
        a.process_into(&input, &mut whole);
        for chunk in input.chunks(128) {
            b.process_into(chunk, &mut partial);
            joined.extend_from_slice(&partial);
        }
        assert_eq!(whole, joined);
        assert!(whole[..960].windows(2).all(|p| p[0] <= p[1]));
        for frame in whole.chunks_exact(2) {
            assert_eq!(frame[0], frame[1]);
        }
        assert_eq!(whole[958], whole[960]);
        assert!(whole[0] > 0.01 && whole[0] < whole[958]);
    }

    #[test]
    fn settled_levels_follow_gain_and_meters_measure_before_clamp() {
        for db in [-24.0, -6.0, 0.0, 6.0, 24.0] {
            let mut stage = GainStage::new(1000);
            let mut output = Vec::new();
            stage.set_gain_db(db);
            stage.process_into(&[0.0; 20], &mut output);
            stage.take_metrics();
            stage.process_into(&[0.5; 100], &mut output);
            let meter = stage.take_metrics();
            let expected = 20.0 * 0.5_f64.log10() + db;
            assert!((meter.peak_dbfs.unwrap() - expected).abs() < 1e-10);
            assert!((meter.rms_dbfs.unwrap() - expected).abs() < 1e-10);
            assert_eq!(meter.clipped_samples, if expected > 0.0 { 100 } else { 0 });
            assert!(output.iter().all(|x| x.is_finite() && x.abs() <= 1.0));
        }
    }

    #[test]
    fn silence_nonfinite_extremes_and_interval_reset() {
        let mut stage = GainStage::new(1000);
        let mut output = Vec::new();
        assert!(stage.take_metrics().peak_dbfs.is_none());
        stage.process_into(&[0.0; 20], &mut output);
        let silent = stage.take_metrics();
        assert_eq!(silent.samples, 20);
        assert!(silent.rms_dbfs.is_none());
        stage.set_gain_db(24.0);
        stage.process_into(&[0.0; 20], &mut output);
        stage.take_metrics();
        stage.process_into(
            &[
                f64::NAN,
                f64::INFINITY,
                f64::NEG_INFINITY,
                f64::MAX,
                -f64::MAX,
                0.0,
            ],
            &mut output,
        );
        assert_eq!(output, [0.0, 0.0, 0.0, 1.0, -1.0, 0.0]);
        let meter = stage.take_metrics();
        assert_eq!(meter.non_finite_samples, 3);
        assert_eq!(meter.clipped_samples, 2);
        assert!(meter.peak_dbfs.unwrap().is_finite());
        assert!(meter.rms_dbfs.unwrap().is_finite());
        let empty = stage.take_metrics();
        assert_eq!(empty.samples, 0);
        assert_eq!(empty.clipped_samples, 0);
        assert_eq!(empty.non_finite_samples, 0);
        assert_eq!(empty.clipped_total, 2);
        assert_eq!(empty.non_finite_total, 3);
    }

    #[test]
    fn rms_counts_zero_samples_and_uses_both_channels() {
        let mut stage = GainStage::new(48000);
        let mut output = Vec::new();
        stage.process_into(&[1.0, 0.0, 1.0, 0.0], &mut output);
        let meter = stage.take_metrics();
        assert_eq!(meter.peak_dbfs, Some(0.0));
        assert!((meter.rms_dbfs.unwrap() + 3.01029995664).abs() < 1e-10);
        assert_eq!(meter.clipped_samples, 0);
    }

    // ---- configurable boost ceiling (#max-gain) ----

    #[test]
    fn sanitize_max_gain_clamps_to_span_and_recovers_non_finite() {
        // In-range requests pass through untouched.
        assert_eq!(sanitize_max_gain_db(24.0), 24.0);
        assert_eq!(sanitize_max_gain_db(36.0), 36.0);
        assert_eq!(sanitize_max_gain_db(MIN_MAX_GAIN_DB), MIN_MAX_GAIN_DB);
        assert_eq!(sanitize_max_gain_db(MAX_MAX_GAIN_DB), MAX_MAX_GAIN_DB);
        // Out-of-span requests clamp to the nearest bound.
        assert_eq!(sanitize_max_gain_db(1000.0), MAX_MAX_GAIN_DB);
        assert_eq!(sanitize_max_gain_db(-5.0), MIN_MAX_GAIN_DB);
        assert_eq!(sanitize_max_gain_db(MAX_MAX_GAIN_DB + 0.01), MAX_MAX_GAIN_DB);
        // Non-finite requests fall back to the default rather than poisoning the range.
        for bad in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            assert_eq!(sanitize_max_gain_db(bad), DEFAULT_MAX_GAIN_DB);
        }
    }

    #[test]
    fn valid_gain_follows_the_ceiling_argument() {
        // Floor is fixed and inclusive; the ceiling is whatever is passed, inclusive.
        assert!(valid_gain_db(GAIN_FLOOR_DB, 24.0));
        assert!(valid_gain_db(24.0, 24.0));
        assert!(!valid_gain_db(24.01, 24.0));
        assert!(!valid_gain_db(GAIN_FLOOR_DB - 0.01, 24.0));
        // A raised ceiling admits gains the default would reject.
        assert!(valid_gain_db(30.0, 36.0));
        assert!(valid_gain_db(36.0, 36.0));
        assert!(!valid_gain_db(36.01, 36.0));
        // A lowered ceiling rejects gains the default would admit.
        assert!(!valid_gain_db(18.0, 12.0));
        assert!(valid_gain_db(12.0, 12.0));
        // Non-finite is never valid, whatever the ceiling.
        for bad in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            assert!(!valid_gain_db(bad, 60.0));
        }
    }

    #[test]
    fn clamp_gain_clips_above_ceiling_and_below_floor_only() {
        // Above the ceiling → down to it; in range → unchanged; below the floor → up to it.
        assert_eq!(clamp_gain_db(30.0, 12.0), 12.0);
        assert_eq!(clamp_gain_db(6.0, 12.0), 6.0);
        assert_eq!(clamp_gain_db(12.0, 12.0), 12.0);
        assert_eq!(clamp_gain_db(GAIN_FLOOR_DB - 10.0, 12.0), GAIN_FLOOR_DB);
        // A raised ceiling leaves a once-too-high gain alone.
        assert_eq!(clamp_gain_db(30.0, 36.0), 30.0);
        // An out-of-span / non-finite ceiling is sanitised first, so the clamp never panics and
        // the range stays non-empty.
        assert_eq!(clamp_gain_db(100.0, 1000.0), MAX_MAX_GAIN_DB);
        assert_eq!(clamp_gain_db(10.0, f64::NAN), 10.0); // NaN ceiling → default 24, 10 unchanged
        assert_eq!(clamp_gain_db(30.0, f64::NAN), DEFAULT_MAX_GAIN_DB);
    }

    #[test]
    fn default_ceiling_matches_the_legacy_fixed_maximum() {
        // A fresh stage behaves exactly like the old hard-coded [-24, 24] range.
        let mut stage = GainStage::new(48000);
        assert_eq!(stage.max_gain_db(), DEFAULT_MAX_GAIN_DB);
        assert!(stage.set_gain_db(24.0));
        assert!(!stage.set_gain_db(24.01));
        assert!(stage.set_gain_db(-24.0));
        assert!(!stage.set_gain_db(-24.01));
    }

    #[test]
    fn raising_ceiling_admits_a_previously_rejected_boost() {
        let mut stage = GainStage::new(48000);
        // Rejected at the default ceiling, and the gain is untouched.
        assert!(!stage.set_gain_db(30.0));
        assert_eq!(stage.gain_db(), 0.0);
        // Raise the ceiling; the same request is now accepted.
        assert_eq!(stage.set_max_gain_db(36.0), 0.0); // raising alone does not move the gain
        assert_eq!(stage.max_gain_db(), 36.0);
        assert!(stage.set_gain_db(30.0));
        assert_eq!(stage.gain_db(), 30.0);
        // Still bounded by the new ceiling.
        assert!(stage.set_gain_db(36.0));
        assert!(!stage.set_gain_db(36.01));
    }

    #[test]
    fn lowering_ceiling_clips_a_too_loud_gain_and_spares_a_lower_one() {
        // Gain above the new ceiling is clipped down to it; the method returns the clipped value.
        let mut stage = GainStage::new(48000);
        stage.set_max_gain_db(48.0);
        assert!(stage.set_gain_db(40.0));
        assert_eq!(stage.set_max_gain_db(12.0), 12.0);
        assert_eq!(stage.gain_db(), 12.0);

        // Gain already within the new ceiling is left exactly as it was ("unchanged otherwise").
        let mut stage = GainStage::new(48000);
        assert!(stage.set_gain_db(6.0));
        assert_eq!(stage.set_max_gain_db(12.0), 6.0);
        assert_eq!(stage.gain_db(), 6.0);

        // Gain exactly at the new ceiling is the boundary case: equal, so unchanged (no clip).
        let mut stage = GainStage::new(48000);
        assert!(stage.set_gain_db(12.0));
        assert_eq!(stage.set_max_gain_db(12.0), 12.0);
        assert_eq!(stage.gain_db(), 12.0);
    }

    #[test]
    fn clipping_down_ramps_to_the_new_ceiling_factor() {
        // Clipping the gain down must settle at the new ceiling's linear factor, not jump.
        let mut stage = GainStage::new(1000);
        let mut output = Vec::new();
        stage.set_max_gain_db(40.0);
        stage.set_gain_db(40.0);
        stage.process_into(&[0.001; 200], &mut output); // let the ramp up settle
        stage.take_metrics();

        stage.set_max_gain_db(6.0);
        assert_eq!(stage.gain_db(), 6.0);
        // First processed frame is still ramping down (above the 6 dB target), last has settled.
        stage.process_into(&[0.001; 200], &mut output);
        let settled = 0.001 * 10.0_f64.powf(6.0 / 20.0);
        assert!((output.last().copied().unwrap() as f64 - settled).abs() < 1e-7);
        assert!((output[0] as f64) > settled); // came down through the ramp, not instantly
    }

    #[test]
    fn set_max_gain_sanitises_out_of_span_and_non_finite_requests() {
        // An absurd request is clamped to the ceiling cap, which then bounds the gain.
        let mut stage = GainStage::new(48000);
        stage.set_max_gain_db(1_000_000.0);
        assert_eq!(stage.max_gain_db(), MAX_MAX_GAIN_DB);
        assert!(stage.set_gain_db(MAX_MAX_GAIN_DB));
        assert!(!stage.set_gain_db(MAX_MAX_GAIN_DB + 0.01));

        // A negative request collapses the ceiling to 0 dB, clipping any positive gain to unity.
        let mut stage = GainStage::new(48000);
        stage.set_gain_db(10.0);
        assert_eq!(stage.set_max_gain_db(-100.0), 0.0);
        assert_eq!(stage.max_gain_db(), MIN_MAX_GAIN_DB);
        assert_eq!(stage.gain_db(), 0.0);
        // Attenuation is still available below the collapsed ceiling.
        assert!(stage.set_gain_db(-24.0));

        // A non-finite request falls back to the default ceiling and clips accordingly.
        let mut stage = GainStage::new(48000);
        stage.set_max_gain_db(48.0);
        stage.set_gain_db(40.0);
        assert_eq!(stage.set_max_gain_db(f64::NAN), DEFAULT_MAX_GAIN_DB);
        assert_eq!(stage.max_gain_db(), DEFAULT_MAX_GAIN_DB);
        assert_eq!(stage.gain_db(), DEFAULT_MAX_GAIN_DB); // 40 clipped down to the default 24
    }

    #[test]
    fn raising_the_ceiling_back_up_does_not_restore_a_clipped_gain() {
        // Clipping is a one-way move: once the gain is pulled down, widening the ceiling again
        // leaves it where it was clipped to — it is now the user's live setting.
        let mut stage = GainStage::new(48000);
        stage.set_max_gain_db(40.0);
        stage.set_gain_db(40.0);
        stage.set_max_gain_db(12.0);
        assert_eq!(stage.gain_db(), 12.0);
        assert_eq!(stage.set_max_gain_db(40.0), 12.0);
        assert_eq!(stage.gain_db(), 12.0);
    }
}
