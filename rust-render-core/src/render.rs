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

/// The world-lock crossfade mixer: routes `n_in` inputs into `n_bus` buses, each channel's gain
/// ramping toward a one-hot on its current target bus over `fade` samples. Bit-exact port of
/// `live_render.BusMixer` (ascending-`c` accumulation == numpy `einsum("cbn,nc->bn")`).
pub struct BusMixerState {
    n_in: usize,
    n_bus: usize,
    fade: f64,
    gain: Vec<f64>, // row-major [c*n_bus + b]
    started: bool,
}

impl BusMixerState {
    pub fn new(n_in: usize, n_bus: usize, fade_samples: i64) -> Self {
        Self {
            n_in,
            n_bus,
            fade: fade_samples.max(1) as f64,
            gain: vec![0.0; n_in * n_bus],
            started: false,
        }
    }

    /// `pcm` row-major `(block, n_in)` [`n*n_in + c`] + per-channel `targets` → `bus_in` row-major
    /// `(n_bus, block)` [`b*block + n`], written into `bus_in` (which is zeroed here).
    pub fn mix_into(&mut self, pcm: &[f64], block: usize, targets: &[usize], bus_in: &mut [f64]) {
        assert_eq!(pcm.len(), block * self.n_in);
        assert_eq!(targets.len(), self.n_in);
        assert_eq!(bus_in.len(), self.n_bus * block);
        let mut desired = vec![0.0f64; self.n_in * self.n_bus];
        for (c, &t) in targets.iter().enumerate() {
            assert!(t < self.n_bus, "target bus out of range");
            desired[c * self.n_bus + t] = 1.0;
        }
        if !self.started {
            self.gain.copy_from_slice(&desired);
            self.started = true;
        }
        for v in bus_in.iter_mut() {
            *v = 0.0;
        }
        let mut new_gain = vec![0.0f64; self.n_in * self.n_bus];
        // c outer so the += into bus_in[b,n] accumulates c ascending == einsum's contraction order.
        for c in 0..self.n_in {
            for b in 0..self.n_bus {
                let start = self.gain[c * self.n_bus + b];
                let des = desired[c * self.n_bus + b];
                let step = (if des >= start { 1.0 } else { -1.0 }) / self.fade;
                let (lo, hi) = if des >= start { (start, des) } else { (des, start) };
                let mut last = start;
                for n in 0..block {
                    let r = (start + step * ((n + 1) as f64)).max(lo).min(hi);
                    last = r;
                    bus_in[b * block + n] += r * pcm[n * self.n_in + c];
                }
                new_gain[c * self.n_bus + b] = last;
            }
        }
        self.gain = new_gain;
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
    // scratch reused per block
    pcm_dir: Vec<f64>, // (block, k)
    bus_in: Vec<f64>,  // (M, block)
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
        })
    }

    /// Nearest grid bus to `az` (deg). `np.argmin` semantics: first minimum wins.
    fn nearest_grid(&self, az: f64) -> usize {
        let mut best_i = 0usize;
        let mut best_d = f64::INFINITY;
        for (m, &g) in self.grid_az.iter().enumerate() {
            let d = wrapped_deg(g - az).abs();
            if d < best_d {
                best_d = d;
                best_i = m;
            }
        }
        best_i
    }

    /// Render one block. `pcm` row-major `(block, n_in)`, `az` the per-input-channel head-relative
    /// azimuth (deg). Writes interleaved stereo into `out` (`block*2`, [`n*2 + ear`]).
    pub fn process_into(&mut self, pcm: &[f64], az: &[f64], out: &mut [f64]) {
        assert_eq!(pcm.len(), self.block * self.n_in);
        assert_eq!(az.len(), self.n_in);
        assert_eq!(out.len(), self.block * 2);
        let k = self.dir_idx.len();
        let targets: Vec<usize> =
            self.dir_idx.iter().map(|&i| self.nearest_grid(az[i])).collect();
        // pcm_dir[n, j] = pcm[n, dir_idx[j]]
        for n in 0..self.block {
            for j in 0..k {
                self.pcm_dir[n * k + j] = pcm[n * self.n_in + self.dir_idx[j]];
            }
        }
        self.mixer.mix_into(&self.pcm_dir, self.block, &targets, &mut self.bus_in);
        let mut l = vec![0.0; self.block];
        let mut r = vec![0.0; self.block];
        self.conv.process_into(&self.bus_in, &mut l, &mut r);
        match &mut self.lfe {
            Lfe::None => {}
            Lfe::Brir { conv, idx } => {
                let mut lfe_in = vec![0.0; self.block];
                for n in 0..self.block {
                    lfe_in[n] = pcm[n * self.n_in + *idx];
                }
                let mut ll = vec![0.0; self.block];
                let mut lr = vec![0.0; self.block];
                conv.process_into(&lfe_in, &mut ll, &mut lr);
                for n in 0..self.block {
                    l[n] += ll[n];
                    r[n] += lr[n];
                }
            }
            Lfe::Flat { idx, gl, gr } => {
                for n in 0..self.block {
                    let s = pcm[n * self.n_in + *idx];
                    l[n] += s * *gl;
                    r[n] += s * *gr;
                }
            }
        }
        for n in 0..self.block {
            out[n * 2] = l[n];
            out[n * 2 + 1] = r[n];
        }
    }
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
        assert_eq!(gr.nearest_grid(5.0), 0);
    }
}
