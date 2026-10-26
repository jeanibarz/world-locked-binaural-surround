//! The slow PI drift controller for the output resampler (BUG #192).
//!
//! The capture source (a *virtual* device — e.g. VoiceMeeter Input) and the render output (a
//! *hardware* device — e.g. a FiiO K11 DAC) run on INDEPENDENT clocks. Both are nominally 48 kHz,
//! but their true rates differ by tens of ppm, so a static-cushion [`crate::CushionFifo`] between
//! them slowly drains (or fills) until it underruns (or overflows) and re-primes — the recurring
//! sawtooth. The cushion math is correct; what it lacked was drift *correction*.
//!
//! This controller closes that loop. It watches the FIFO fill and nudges the output resampler's
//! ratio so the consumer draws slightly fewer (or more) source frames per output frame, holding the
//! fill at the cushion setpoint. It is a textbook PI controller on the fill error:
//!
//! * The plant (FIFO fill) is a pure integrator of the (produced − consumed) frame-rate difference,
//!   so **proportional** action alone would leave a steady-state offset proportional to the drift.
//!   The **integral** term drives that residual offset to zero — the correction settles at exactly
//!   the clock drift and the fill returns to the setpoint.
//! * Gains are deliberately tiny and the correction is clamped to a few hundred ppm — an order of
//!   magnitude above real drift (tens of ppm) for recovery headroom, yet small enough that the pitch
//!   shift is inaudible and the ratio never swings fast (journal: "slow, no fast ratio swings").
//!
//! The controller is pure and clock-free (one `update` per resampler chunk, `dt` folded into the
//! gains), so it unit-tests natively against a simulated drift — unlike the Windows-only WASAPI glue
//! that drives it.

/// Default proportional gain, in correction-per-unit-normalised-fill-error. A full setpoint of error
/// (fill at 0 or at 2×setpoint) asks for `KP` of proportional correction before clamping.
const DEFAULT_KP: f64 = 0.002;
/// Default integral gain per `update`. Much smaller than `KP`: the integral is what cancels the
/// standing drift, and it must ramp in over many chunks so the ratio creeps rather than jumps.
const DEFAULT_KI: f64 = 2.0e-5;
/// Hard clamp on the relative correction, each side of 1.0 (± this). 0.004 = 4000 ppm ≈ 7 cents at
/// the extreme — reached only transiently while recovering; real steady-state drift sits at tens of
/// ppm, far inside this bound. The resampler must be built to allow at least this much relative
/// swing (see [`MAX_RESAMPLE_RATIO_RELATIVE`]).
const DEFAULT_MAX_REL: f64 = 0.004;

/// The relative-ratio bound to construct the output resampler with (`max_resample_ratio_relative`).
/// Must exceed [`DriftController`]'s clamp so a saturated correction is always a legal ratio; the
/// margin also covers a one-off nominal-rate change. rubato requires this be ≥ 1.0.
pub const MAX_RESAMPLE_RATIO_RELATIVE: f64 = 1.05;

/// A slow PI controller that converts a measured FIFO fill into a relative resample ratio
/// (`1.0` = the nominal ratio) for the output resampler.
#[derive(Debug, Clone)]
pub struct DriftController {
    setpoint: f64,
    kp: f64,
    ki: f64,
    max_rel: f64,
    integral: f64,
    last_correction: f64,
}

impl DriftController {
    /// `setpoint` is the target FIFO fill in frames — the cushion depth the FIFO prefills to. Uses
    /// the tuned default gains. A non-positive setpoint is floored to 1.0 so the normalisation never
    /// divides by zero.
    pub fn new(setpoint_frames: f64) -> Self {
        Self::with_gains(setpoint_frames, DEFAULT_KP, DEFAULT_KI, DEFAULT_MAX_REL)
    }

    /// As [`new`](Self::new) but with explicit gains and clamp — used by the controller unit tests
    /// to exercise convergence without waiting out the production (deliberately slow) time constant.
    pub fn with_gains(setpoint_frames: f64, kp: f64, ki: f64, max_rel: f64) -> Self {
        Self {
            setpoint: setpoint_frames.max(1.0),
            kp,
            ki,
            max_rel: max_rel.max(0.0),
            integral: 0.0,
            last_correction: 0.0,
        }
    }

    /// Feed the current FIFO fill (frames) and get back the relative ratio to hand the resampler via
    /// `set_resample_ratio_relative(ret, true)`. Guaranteed to land in `[1 - max_rel, 1 + max_rel]`.
    ///
    /// Sign: when the fill is BELOW the setpoint the source is being drained faster than it is
    /// filled (the output clock is fast), so the correction goes positive — a larger ratio makes the
    /// resampler draw fewer source frames per output frame, letting the FIFO refill (and vice-versa).
    pub fn update(&mut self, fill_frames: f64) -> f64 {
        // Normalise the error by the setpoint so the gains are independent of block / cushion size.
        let err = (fill_frames - self.setpoint) / self.setpoint;
        self.integral += err;
        // Anti-windup: bound the integral so its term alone can never exceed the clamp. Without this
        // a long saturation (e.g. a startup transient) would wind the integral far past the clamp and
        // then take just as long to unwind, overshooting the setpoint.
        if self.ki > 0.0 {
            let int_limit = self.max_rel / self.ki;
            self.integral = self.integral.clamp(-int_limit, int_limit);
        }
        // Too full (err > 0) → consume more source → smaller ratio → negative correction, hence the
        // leading minus on the whole PI sum.
        let raw = -(self.kp * err + self.ki * self.integral);
        self.last_correction = raw.clamp(-self.max_rel, self.max_rel);
        1.0 + self.last_correction
    }

    /// The last correction expressed in ppm (signed) — a diagnostic the engine surfaces to the
    /// cockpit so the operator can watch the loop hold the fill steady.
    pub fn correction_ppm(&self) -> f64 {
        self.last_correction * 1.0e6
    }

    /// The target fill the loop is holding to, in frames.
    pub fn setpoint(&self) -> f64 {
        self.setpoint
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Simulate the real closed loop: the FIFO fill integrates (source produced − resampler
    /// consumed) each chunk, where the output clock is drifted `drift` relative to the source. The
    /// controller must pull the fill back to the setpoint and hold it there, with the steady-state
    /// correction settling at the drift (that is what an integrator-plant PI loop does).
    fn simulate(drift: f64, ticks: usize) -> (Vec<f64>, DriftController) {
        let out_per_tick = 1024.0; // device output frames per chunk
        let setpoint = 2048.0; // a 2-block cushion at block = 1024
        // Faster-than-production gains so the test converges in a few thousand ticks instead of the
        // production minutes; the dynamics (and the steady-state result) are identical.
        let mut ctrl = DriftController::with_gains(setpoint, 0.02, 2.0e-3, 0.02);
        let mut fill = setpoint;
        let mut fills = Vec::with_capacity(ticks);
        for _ in 0..ticks {
            let rel = ctrl.update(fill);
            // Source produces `out_per_tick` input frames per wall tick; the resampler draws
            // out_per_tick*(1+drift)/rel input frames (the device, drifted, wants that many output
            // frames and the ratio `rel` sets input-per-output).
            let consumed = out_per_tick * (1.0 + drift) / rel;
            fill += out_per_tick - consumed;
            fill = fill.max(0.0); // a real FIFO can't go negative (it would underrun)
            fills.push(fill);
        }
        (fills, ctrl)
    }

    #[test]
    fn holds_fill_at_setpoint_under_positive_drift() {
        // Output clock runs 120 ppm fast → it drains the FIFO; the loop must refill and hold.
        let (fills, ctrl) = simulate(120.0e-6, 20_000);
        let setpoint = ctrl.setpoint();
        let settled = &fills[fills.len() - 2_000..];
        let max_dev = settled.iter().map(|f| (f - setpoint).abs()).fold(0.0f64, f64::max);
        assert!(max_dev < 20.0, "settled fill strayed {max_dev:.1} frames from setpoint");
        // The correction settles at the drift that caused the error (sign: fast output → positive).
        assert!(
            (ctrl.correction_ppm() - 120.0).abs() < 10.0,
            "correction settled at {:.1} ppm, expected ≈120",
            ctrl.correction_ppm()
        );
    }

    #[test]
    fn holds_fill_at_setpoint_under_negative_drift() {
        // Output clock runs 120 ppm slow → it over-fills the FIFO; the loop must bleed it back down.
        let (fills, ctrl) = simulate(-120.0e-6, 20_000);
        let setpoint = ctrl.setpoint();
        let settled = &fills[fills.len() - 2_000..];
        let max_dev = settled.iter().map(|f| (f - setpoint).abs()).fold(0.0f64, f64::max);
        assert!(max_dev < 20.0, "settled fill strayed {max_dev:.1} frames from setpoint");
        assert!((ctrl.correction_ppm() + 120.0).abs() < 10.0, "correction {:.1} ppm", ctrl.correction_ppm());
    }

    #[test]
    fn correction_is_always_within_the_clamp() {
        // Even a wild, sustained drift the loop can't fully cancel must never produce an out-of-range
        // ratio (which would panic the resampler's relative-ratio bound).
        let mut ctrl = DriftController::new(2048.0);
        for fill in [0.0, 1.0, 4096.0, 1.0e9, -5.0] {
            let rel = ctrl.update(fill);
            assert!((0.996..=1.004).contains(&rel), "ratio {rel} out of clamp for fill {fill}");
        }
    }

    #[test]
    fn neutral_at_setpoint() {
        // Fill exactly at setpoint with no accumulated history → no correction.
        let mut ctrl = DriftController::new(2048.0);
        assert!((ctrl.update(2048.0) - 1.0).abs() < 1e-12);
    }
}
