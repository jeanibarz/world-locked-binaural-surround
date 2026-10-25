//! World-lock render core — R0 strangler slice 1 (RFC: standalone-distributable-and-rust-engine).
//!
//! Ports the per-block *steering + crossfade* primitives from `live_render.py` to Rust, **bit-exact**
//! so the Python parity test (exact equality on random inputs) passes. These run every block and
//! hold the GIL in the Python engine (the journal-76 render-worker starvation); moving them to Rust
//! is the first real step toward the pure-Rust engine. Convolution is already Rust (binaural_kernel);
//! this is the glue around it.

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

#[pymodule]
fn render_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(wrapped_deg_py, m)?)?;
    m.add_function(wrap_pyfunction!(nearest_brir_index, m)?)?;
    m.add_function(wrap_pyfunction!(gain_ramp, m)?)?;
    Ok(())
}
