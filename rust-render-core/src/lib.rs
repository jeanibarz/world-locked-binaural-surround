//! World-lock render core — R0 strangler slice 1 (RFC: standalone-distributable-and-rust-engine).
//!
//! Ports the per-block *steering + crossfade* primitives from `live_render.py` to Rust, **bit-exact**
//! so the Python parity test (exact equality on random inputs) passes. These run every block and
//! hold the GIL in the Python engine (the journal-76 render-worker starvation); moving them to Rust
//! is the first real step toward the pure-Rust engine. Convolution is already Rust (binaural_kernel);
//! this is the glue around it.

use numpy::ndarray::Array2;
use numpy::{IntoPyArray, PyArray2, PyReadonlyArray2};
use pyo3::prelude::*;

/// Signed smallest angle (deg) for `delta` wrapped to (-180, 180].
/// Python uses a *floored* `%` (sign of divisor); Rust's `%` is truncated, so use `rem_euclid`.
#[inline]
fn wrapped_deg(delta: f64) -> f64 {
    (delta + 180.0).rem_euclid(360.0) - 180.0
}

#[pyfunction]
fn wrapped_deg_py(delta: f64) -> f64 {
    wrapped_deg(delta)
}

/// Channel index of the ring BRIR whose azimuth is nearest `source_az_deg`.
/// `azimuths` is `(index, az_deg)` pairs in RING order (same as the Python dict's insertion order);
/// the `d < best - 1e-9` guard reproduces Python's first-in-order tie resolution exactly.
#[pyfunction]
fn nearest_brir_index(azimuths: Vec<(i64, f64)>, source_az_deg: f64) -> PyResult<i64> {
    let mut best_i: Option<i64> = None;
    let mut best_d: Option<f64> = None;
    for (i, az) in azimuths {
        let d = wrapped_deg(source_az_deg - az).abs();
        if best_d.is_none() || d < best_d.unwrap() - 1e-9 {
            best_i = Some(i);
            best_d = Some(d);
        }
    }
    best_i.ok_or_else(|| pyo3::exceptions::PyValueError::new_err("empty azimuths"))
}

/// Per-sample gain across one block ramping `start`→`end` over `fade_samples`, clamped.
/// Matches `WorldLockedSource._gain_ramp`: `start + step*arange(1, block+1)` then `np.clip`.
#[pyfunction]
fn gain_ramp(start: f64, end: f64, fade_samples: f64, block: usize) -> Vec<f64> {
    let step = (if end >= start { 1.0 } else { -1.0 }) / fade_samples;
    let (lo, hi) = if end >= start { (start, end) } else { (end, start) };
    (1..=block)
        .map(|n| {
            let v = start + step * (n as f64);
            v.max(lo).min(hi) // == np.clip(v, lo, hi)
        })
        .collect()
}

/// The per-block world-lock bus mix (R0 slice 2) — a stateful crossfade router, bit-exact port of
/// `live_render.BusMixer`. Routes `n_in` input channels into `n_bus` BRIR buses, each channel's gain
/// ramping toward a one-hot on its current target bus over `fade` samples. `bus_in[b,n] = Σ_c
/// ramp[c,b,n]·pcm[n,c]`. The ramp is elementwise (bit-exact like gain_ramp); the sum over `c` is
/// accumulated ascending to match numpy `einsum("cbn,nc->bn")`'s (optimize=False) order exactly.
#[pyclass]
struct BusMixer {
    n_in: usize,
    n_bus: usize,
    fade: f64,
    gain: Vec<f64>, // row-major [c*n_bus + b]
    started: bool,
}

#[pymethods]
impl BusMixer {
    #[new]
    #[pyo3(signature = (n_channels, n_buses=None, *, fade_samples))]
    fn new(n_channels: usize, n_buses: Option<usize>, fade_samples: i64) -> Self {
        let n_bus = n_buses.unwrap_or(n_channels);
        BusMixer {
            n_in: n_channels,
            n_bus,
            fade: fade_samples.max(1) as f64,
            gain: vec![0.0; n_channels * n_bus],
            started: false,
        }
    }

    /// `pcm` (block, n_in) + `targets` (n_in,) → `bus_in` (n_bus, block).
    fn mix<'py>(
        &mut self,
        py: Python<'py>,
        pcm: PyReadonlyArray2<'py, f64>,
        targets: Vec<i64>,
    ) -> PyResult<Bound<'py, PyArray2<f64>>> {
        let pcm = pcm.as_array();
        let block = pcm.shape()[0];
        if pcm.shape()[1] != self.n_in || targets.len() != self.n_in {
            return Err(pyo3::exceptions::PyValueError::new_err("pcm/targets shape != n_in"));
        }
        let mut desired = vec![0.0f64; self.n_in * self.n_bus];
        for (c, &t) in targets.iter().enumerate() {
            let b = t as usize;
            if b >= self.n_bus {
                return Err(pyo3::exceptions::PyValueError::new_err("target bus out of range"));
            }
            desired[c * self.n_bus + b] = 1.0;
        }
        if !self.started {
            self.gain.copy_from_slice(&desired); // start at full gain, no fade-in (matches Python)
            self.started = true;
        }
        let mut bus_in = vec![0.0f64; self.n_bus * block]; // [b*block + n]
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
                    bus_in[b * block + n] += r * pcm[[n, c]];
                }
                new_gain[c * self.n_bus + b] = last;
            }
        }
        self.gain = new_gain;
        Ok(Array2::from_shape_vec((self.n_bus, block), bus_in)
            .unwrap()
            .into_pyarray_bound(py))
    }
}

#[pymodule]
fn render_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(wrapped_deg_py, m)?)?;
    m.add_function(wrap_pyfunction!(nearest_brir_index, m)?)?;
    m.add_function(wrap_pyfunction!(gain_ramp, m)?)?;
    m.add_class::<BusMixer>()?;
    Ok(())
}
