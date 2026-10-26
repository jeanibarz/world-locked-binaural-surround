//! The full per-block world-lock render in **pure Rust**: `BusMixer` (steering + crossfade) feeding
//! the shared [`binaural_conv`] convolution core, plus LFE — the Rust side of `InterpWorldLocked7p1`
//! (and the nearest `WorldLocked7p1`). This is the engine's hot loop with no Python in it: one
//! `process_block` call does targets → mix → convolve → LFE → stereo.
//!
//! [`BusMixerState`] is the bit-exact mixer math (the `BusMixer` pyclass delegates to it, so there
//! is one implementation). [`GridRender`] composes it with a grid convolver and LFE, matching the
//! Python `InterpWorldLocked7p1.process_block` (grid on the Rust kernel) to floating error.

use binaural_conv::BusConvolverCore;

/// Signed smallest angle (deg) for `delta`, wrapped to (-180, 180] — floored `%` via `rem_euclid`.
#[inline]
pub fn wrapped_deg(delta: f64) -> f64 {
    (delta + 180.0).rem_euclid(360.0) - 180.0
}

/// Replace a non-finite (NaN/±Inf) sample with silence. A single poisoned input sample would
/// otherwise propagate into every bus's convolver state (`0.0 * NaN == NaN`, so even gain-0 buses
/// are hit) and persist in the FFT delay line for the whole IR length — turning one glitch into
/// seconds of dead output. Scrubbing at the input is cheap and keeps the convolver state clean.
#[inline]
fn finite_or_zero(x: f64) -> f64 {
    if x.is_finite() {
        x
    } else {
        0.0
    }
}

/// The world-lock crossfade mixer: routes `n_in` inputs into `n_bus` buses, each channel's gain
/// ramping toward a one-hot on its current target bus over `fade` samples. Bit-exact port of
/// `live_render.BusMixer` (ascending-`c` accumulation == numpy `einsum("cbn,nc->bn")`).
pub struct BusMixerState {
    n_in: usize,
    n_bus: usize,
    fade: f64,
    gain: Vec<f64>, // row-major [c*n_bus + b]
    started: bool,
    // Resident scratch, reused every block so `mix_into` allocates nothing on the real-time path
    // (BUG-13). Both are fully (re)written each call, so no stale state leaks between blocks.
    desired: Vec<f64>,
    new_gain: Vec<f64>,
}

impl BusMixerState {
    pub fn new(n_in: usize, n_bus: usize, fade_samples: i64) -> Self {
        Self {
            n_in,
            n_bus,
            fade: fade_samples.max(1) as f64,
            gain: vec![0.0; n_in * n_bus],
            started: false,
            desired: vec![0.0; n_in * n_bus],
            new_gain: vec![0.0; n_in * n_bus],
        }
    }

    /// `pcm` row-major `(block, n_in)` [`n*n_in + c`] + per-channel `targets` → `bus_in` row-major
    /// `(n_bus, block)` [`b*block + n`], written into `bus_in` (which is zeroed here).
    pub fn mix_into(&mut self, pcm: &[f64], block: usize, targets: &[usize], bus_in: &mut [f64]) {
        assert_eq!(pcm.len(), block * self.n_in);
        assert_eq!(targets.len(), self.n_in);
        assert_eq!(bus_in.len(), self.n_bus * block);
        for v in self.desired.iter_mut() {
            *v = 0.0;
        }
        for (c, &t) in targets.iter().enumerate() {
            assert!(t < self.n_bus, "target bus out of range");
            self.desired[c * self.n_bus + t] = 1.0;
        }
        if !self.started {
            self.gain.copy_from_slice(&self.desired);
            self.started = true;
        }
        for v in bus_in.iter_mut() {
            *v = 0.0;
        }
        // c outer so the += into bus_in[b,n] accumulates c ascending == einsum's contraction order.
        for c in 0..self.n_in {
            for b in 0..self.n_bus {
                let start = self.gain[c * self.n_bus + b];
                let des = self.desired[c * self.n_bus + b];
                let step = (if des >= start { 1.0 } else { -1.0 }) / self.fade;
                let (lo, hi) = if des >= start { (start, des) } else { (des, start) };
                let mut last = start;
                for n in 0..block {
                    let r = (start + step * ((n + 1) as f64)).max(lo).min(hi);
                    last = r;
                    bus_in[b * block + n] += r * pcm[n * self.n_in + c];
                }
                self.new_gain[c * self.n_bus + b] = last;
            }
        }
        std::mem::swap(&mut self.gain, &mut self.new_gain);
    }
}

/// How LFE (non-directional bass) is rendered — matches `WorldLocked7p1`/`InterpWorldLocked7p1`.
pub enum Lfe {
    /// No LFE channel.
    None,
    /// Convolve LFE through its own fixed BRIR (a 1-bus convolver) and sum. `idx` is its input col.
    Brir { conv: BusConvolverCore, idx: usize },
    /// Non-directional flat sum to each ear at a level-matched gain (the LFE-IR L2 norms).
    Flat { idx: usize, gl: f64, gr: f64 },
}

/// Full world-lock render over a dense bus grid: targets (nearest grid bus per directional input) →
/// crossfade mix → batched grid convolution → LFE → stereo. The Rust twin of
/// `InterpWorldLocked7p1.process_block` (with the grid convolution on the native kernel).
pub struct GridRender {
    mixer: BusMixerState,
    conv: BusConvolverCore,
    grid_az: Vec<f64>,
    dir_idx: Vec<usize>,
    n_in: usize,
    block: usize,
    lfe: Lfe,
    // scratch reused per block (BUG-13: nothing allocated on the render path)
    pcm_dir: Vec<f64>, // (block, k)
    bus_in: Vec<f64>,  // (M, block)
    targets: Vec<usize>, // (k) nearest grid bus per directional input
    l: Vec<f64>,       // (block) left ear accumulator
    r: Vec<f64>,       // (block) right ear accumulator
    lfe_in: Vec<f64>,  // (block) LFE input column
    ll: Vec<f64>,      // (block) LFE conv left
    lr: Vec<f64>,      // (block) LFE conv right
}

impl GridRender {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        dense_left: &[f64],
        dense_right: &[f64],
        n_grid: usize,
        n_taps: usize,
        grid_az: Vec<f64>,
        dir_idx: Vec<usize>,
        n_in: usize,
        fade_samples: i64,
        block: usize,
        lfe: Lfe,
    ) -> Result<Self, String> {
        if grid_az.len() != n_grid {
            return Err("grid_az must have n_grid entries".into());
        }
        let conv = BusConvolverCore::new(dense_left, dense_right, n_grid, n_taps, block)?;
        let k = dir_idx.len();
        let mixer = BusMixerState::new(k, n_grid, fade_samples);
        Ok(Self {
            mixer,
            conv,
            grid_az,
            dir_idx,
            n_in,
            block,
            lfe,
            pcm_dir: vec![0.0; block * k],
            bus_in: vec![0.0; n_grid * block],
            targets: vec![0; k],
            l: vec![0.0; block],
            r: vec![0.0; block],
            lfe_in: vec![0.0; block],
            ll: vec![0.0; block],
            lr: vec![0.0; block],
        })
    }

    /// Render one block. `pcm` row-major `(block, n_in)`, `az` the per-input-channel head-relative
    /// azimuth (deg). Writes interleaved stereo into `out` (`block*2`, [`n*2 + ear`]).
    pub fn process_into(&mut self, pcm: &[f64], az: &[f64], out: &mut [f64]) {
        assert_eq!(pcm.len(), self.block * self.n_in);
        assert_eq!(az.len(), self.n_in);
        assert_eq!(out.len(), self.block * 2);
        let k = self.dir_idx.len();
        // nearest grid bus per directional input (into resident `targets`; `grid_az` borrow only).
        // A non-finite azimuth (e.g. a NaN yaw from a failed pose solve) would make every distance
        // comparison false and silently snap the whole field to bus 0; hold the previous target
        // instead so a transient tracker glitch doesn't jerk the image to front-centre.
        for j in 0..k {
            let a = az[self.dir_idx[j]];
            if a.is_finite() {
                self.targets[j] = nearest_grid_in(&self.grid_az, a);
            }
        }
        // pcm_dir[n, j] = pcm[n, dir_idx[j]] — scrub non-finite samples (see `finite_or_zero`).
        for n in 0..self.block {
            for j in 0..k {
                self.pcm_dir[n * k + j] = finite_or_zero(pcm[n * self.n_in + self.dir_idx[j]]);
            }
        }
        self.mixer.mix_into(&self.pcm_dir, self.block, &self.targets, &mut self.bus_in);
        self.conv.process_into(&self.bus_in, &mut self.l, &mut self.r);
        match &mut self.lfe {
            Lfe::None => {}
            Lfe::Brir { conv, idx } => {
                for n in 0..self.block {
                    self.lfe_in[n] = finite_or_zero(pcm[n * self.n_in + *idx]);
                }
                conv.process_into(&self.lfe_in, &mut self.ll, &mut self.lr);
                for n in 0..self.block {
                    self.l[n] += self.ll[n];
                    self.r[n] += self.lr[n];
                }
            }
            Lfe::Flat { idx, gl, gr } => {
                for n in 0..self.block {
                    let s = finite_or_zero(pcm[n * self.n_in + *idx]);
                    self.l[n] += s * *gl;
                    self.r[n] += s * *gr;
                }
            }
        }
        for n in 0..self.block {
            out[n * 2] = self.l[n];
            out[n * 2 + 1] = self.r[n];
        }
    }
}

/// Nearest grid bus to `az` (deg) over `grid_az`. `np.argmin` semantics: first minimum wins.
fn nearest_grid_in(grid_az: &[f64], az: f64) -> usize {
    let mut best_i = 0usize;
    let mut best_d = f64::INFINITY;
    for (m, &g) in grid_az.iter().enumerate() {
        let d = wrapped_deg(g - az).abs();
        if d < best_d {
            best_d = d;
            best_i = m;
        }
    }
    best_i
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mixer_first_block_full_gain_one_hot() {
        // 1 input -> 2 buses, target bus 1 on the first block: full gain immediately (no fade-in).
        let mut m = BusMixerState::new(1, 2, 64);
        let block = 3;
        let pcm = vec![1.0, 2.0, 3.0]; // (block,1)
        let mut bus_in = vec![0.0; 2 * block];
        m.mix_into(&pcm, block, &[1], &mut bus_in);
        // bus0 silent, bus1 == pcm
        assert_eq!(&bus_in[0..3], &[0.0, 0.0, 0.0]);
        assert_eq!(&bus_in[3..6], &[1.0, 2.0, 3.0]);
    }

    #[test]
    fn nearest_grid_argmin_first_wins() {
        let gr = GridRender::new(
            &[1.0, 1.0],
            &[1.0, 1.0],
            2,
            1,
            vec![0.0, 0.0], // identical azimuths -> tie, first index wins
            vec![0],
            1,
            64,
            1,
            Lfe::None,
        )
        .unwrap();
        assert_eq!(nearest_grid_in(&gr.grid_az, 5.0), 0);
    }

    #[test]
    fn non_finite_input_is_scrubbed_and_does_not_persist() {
        // 1 input -> 2 buses, 1-tap identity IRs. A NaN sample in one block must not reach the
        // output, and must not poison the convolver state for the following clean block.
        let mut gr = GridRender::new(
            &[1.0, 1.0], &[1.0, 1.0], 2, 1, vec![0.0, 90.0], vec![0], 1, 1, 2, Lfe::None,
        )
        .unwrap();
        let mut out = vec![0.0; 2 * 2];
        gr.process_into(&[f64::NAN, 1.0], &[0.0], &mut out);
        assert!(out.iter().all(|v| v.is_finite()), "NaN input leaked to output: {out:?}");
        gr.process_into(&[0.5, 0.5], &[0.0], &mut out);
        assert!(out.iter().all(|v| v.is_finite()), "convolver state stayed poisoned: {out:?}");
        assert!(out.iter().any(|v| *v != 0.0), "clean block produced only silence");
    }

    #[test]
    fn non_finite_azimuth_holds_the_last_target() {
        // A NaN/Inf yaw must hold the previous steering target, not silently snap to bus 0.
        let mut gr = GridRender::new(
            &[1.0, 1.0], &[1.0, 1.0], 2, 1, vec![0.0, 90.0], vec![0], 1, 1, 1, Lfe::None,
        )
        .unwrap();
        let mut out = vec![0.0; 2];
        gr.process_into(&[1.0], &[90.0], &mut out); // steer to bus 1
        assert_eq!(gr.targets[0], 1);
        gr.process_into(&[1.0], &[f64::NAN], &mut out); // NaN yaw holds bus 1
        assert_eq!(gr.targets[0], 1, "NaN azimuth snapped the field to bus 0");
    }
}
