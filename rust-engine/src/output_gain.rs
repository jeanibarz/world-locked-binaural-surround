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

/// Accepted output trim range, shared by persistence and request validation.
pub fn valid_gain_db(gain_db: f64) -> bool {
    gain_db.is_finite() && (-24.0..=24.0).contains(&gain_db)
}

pub struct GainStage {
    gain_db: f64,
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

    /// Reject non-finite or out-of-range settings without changing the current ramp.
    pub fn set_gain_db(&mut self, gain_db: f64) -> bool {
        if !valid_gain_db(gain_db) {
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
}
