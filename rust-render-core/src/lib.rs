//! World-lock render core (RFC: standalone-distributable-and-rust-engine).
//!
//! R0 ported the per-block *steering + crossfade* primitives from `live_render.py` to Rust,
//! bit-exact (the Python parity test asserts exact equality on random inputs). This slice grows the
//! crate to the **full per-block render**: [`render::GridRender`] composes the crossfade `BusMixer`
//! with the shared pure-Rust convolution core ([`binaural_conv`]) and LFE, so the hot render loop
//! runs entirely in Rust (one `process_block` call: targets → mix → convolve → LFE → stereo) and no
//! longer threads through the Python interpreter — the real step toward the zero-Python engine.
//! Convolution is the shared native kernel; this is the render around it.

// pyo3 0.22's `#[pyfunction]`/`#[pymethods]` expansion trips clippy's `useless_conversion` on the
// generated result-coercion; it is macro-internal, not our code.
#![allow(clippy::useless_conversion)]

use numpy::ndarray::Array2;
use numpy::{IntoPyArray, PyArray2, PyReadonlyArray1, PyReadonlyArray2};
use pyo3::prelude::*;

use render_core_lib::{wrapped_deg, BusMixerState, GridRender, Lfe};

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
/// `live_render.BusMixer`. Thin PyO3 wrapper over [`BusMixerState`] (the shared mixer math).
#[pyclass]
struct BusMixer {
    state: BusMixerState,
    n_in: usize,
    n_bus: usize,
}

#[pymethods]
impl BusMixer {
    #[new]
    #[pyo3(signature = (n_channels, n_buses=None, *, fade_samples))]
    fn new(n_channels: usize, n_buses: Option<usize>, fade_samples: i64) -> Self {
        let n_bus = n_buses.unwrap_or(n_channels);
        BusMixer { state: BusMixerState::new(n_channels, n_bus, fade_samples), n_in: n_channels, n_bus }
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
        // row-major (block, n_in)
        let mut pcm_rm = Vec::with_capacity(block * self.n_in);
        for n in 0..block {
            for c in 0..self.n_in {
                pcm_rm.push(pcm[[n, c]]);
            }
        }
        let mut tgt = Vec::with_capacity(self.n_in);
        for &t in &targets {
            if t < 0 || t as usize >= self.n_bus {
                return Err(pyo3::exceptions::PyValueError::new_err("target bus out of range"));
            }
            tgt.push(t as usize);
        }
        let mut bus_in = vec![0.0f64; self.n_bus * block];
        self.state.mix_into(&pcm_rm, block, &tgt, &mut bus_in);
        Ok(Array2::from_shape_vec((self.n_bus, block), bus_in).unwrap().into_pyarray_bound(py))
    }
}

/// Full world-lock render over a dense bus grid, in Rust: targets → crossfade mix → native grid
/// convolution → LFE → stereo. The Rust twin of `InterpWorldLocked7p1.process_block` (grid on the
/// native kernel). Construct from the pre-built dense grid (left/right IR banks + grid azimuths);
/// interpolation/baking stays in Python/Track-S — this owns only the per-block render.
#[pyclass]
struct RenderGrid {
    inner: GridRender,
    n_in: usize,
    block: usize,
}

#[pymethods]
impl RenderGrid {
    /// `dense_left`/`dense_right` are the `(M, taps)` grid BRIR banks; `grid_az` the `M` grid
    /// azimuths; `dir_idx` the directional input-channel indices (length `k`); `n_in` the total
    /// input channels. `lfe_mode` ∈ {"none","brir","flat"}; for "brir" pass `lfe_idx` +
    /// `lfe_left`/`lfe_right` (the LFE IR), for "flat" pass `lfe_idx` + `lfe_gain_l`/`lfe_gain_r`.
    #[new]
    #[pyo3(signature = (dense_left, dense_right, grid_az, dir_idx, n_in, fade_samples, block,
                        lfe_mode="none", lfe_idx=None, lfe_left=None, lfe_right=None,
                        lfe_gain_l=0.0, lfe_gain_r=0.0))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        dense_left: PyReadonlyArray2<f64>,
        dense_right: PyReadonlyArray2<f64>,
        grid_az: Vec<f64>,
        dir_idx: Vec<usize>,
        n_in: usize,
        fade_samples: i64,
        block: usize,
        lfe_mode: &str,
        lfe_idx: Option<usize>,
        lfe_left: Option<PyReadonlyArray1<f64>>,
        lfe_right: Option<PyReadonlyArray1<f64>>,
        lfe_gain_l: f64,
        lfe_gain_r: f64,
    ) -> PyResult<Self> {
        let dl = dense_left.as_array();
        let dr = dense_right.as_array();
        if dl.shape() != dr.shape() {
            return Err(pyo3::exceptions::PyValueError::new_err("dense left/right shape mismatch"));
        }
        let (m, taps) = (dl.shape()[0], dl.shape()[1]);
        let mut left = Vec::with_capacity(m * taps);
        let mut right = Vec::with_capacity(m * taps);
        for ch in 0..m {
            for t in 0..taps {
                left.push(dl[[ch, t]]);
                right.push(dr[[ch, t]]);
            }
        }
        let lfe = match lfe_mode {
            "none" => Lfe::None,
            "flat" => {
                let idx = lfe_idx
                    .ok_or_else(|| pyo3::exceptions::PyValueError::new_err("flat LFE needs lfe_idx"))?;
                Lfe::Flat { idx, gl: lfe_gain_l, gr: lfe_gain_r }
            }
            "brir" => {
                let idx = lfe_idx
                    .ok_or_else(|| pyo3::exceptions::PyValueError::new_err("brir LFE needs lfe_idx"))?;
                let (ll, lr) = (
                    lfe_left.ok_or_else(|| {
                        pyo3::exceptions::PyValueError::new_err("brir LFE needs lfe_left")
                    })?,
                    lfe_right.ok_or_else(|| {
                        pyo3::exceptions::PyValueError::new_err("brir LFE needs lfe_right")
                    })?,
                );
                let ll = ll.as_array();
                let lr = lr.as_array();
                let lt = ll.len();
                let lv: Vec<f64> = (0..lt).map(|i| ll[i]).collect();
                let rv: Vec<f64> = (0..lt).map(|i| lr[i]).collect();
                let conv = binaural_conv::BusConvolverCore::new(&lv, &rv, 1, lt, block)
                    .map_err(pyo3::exceptions::PyValueError::new_err)?;
                Lfe::Brir { conv, idx }
            }
            other => {
                return Err(pyo3::exceptions::PyValueError::new_err(format!(
                    "unknown lfe_mode {other:?} (want none|brir|flat)"
                )))
            }
        };
        let inner = GridRender::new(&left, &right, m, taps, grid_az, dir_idx, n_in, fade_samples, block, lfe)
            .map_err(pyo3::exceptions::PyValueError::new_err)?;
        Ok(Self { inner, n_in, block })
    }

    #[getter]
    fn block_size(&self) -> usize {
        self.block
    }

    /// `pcm` (block, n_in), `azimuths` (n_in,) head-relative deg → `(block, 2)` stereo.
    fn process_block<'py>(
        &mut self,
        py: Python<'py>,
        pcm: PyReadonlyArray2<'py, f64>,
        azimuths: PyReadonlyArray1<'py, f64>,
    ) -> PyResult<Bound<'py, PyArray2<f64>>> {
        let pcm = pcm.as_array();
        let az = azimuths.as_array();
        if pcm.shape()[0] != self.block || pcm.shape()[1] != self.n_in {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "pcm must be ({}, {})",
                self.block, self.n_in
            )));
        }
        if az.len() != self.n_in {
            return Err(pyo3::exceptions::PyValueError::new_err("azimuths must have n_in entries"));
        }
        let mut pcm_rm = Vec::with_capacity(self.block * self.n_in);
        for n in 0..self.block {
            for c in 0..self.n_in {
                pcm_rm.push(pcm[[n, c]]);
            }
        }
        let az_v: Vec<f64> = (0..self.n_in).map(|i| az[i]).collect();
        let mut out = vec![0.0f64; self.block * 2];
        py.allow_threads(|| self.inner.process_into(&pcm_rm, &az_v, &mut out));
        Ok(Array2::from_shape_vec((self.block, 2), out).unwrap().into_pyarray_bound(py))
    }
}

#[pymodule]
fn render_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(wrapped_deg_py, m)?)?;
    m.add_function(wrap_pyfunction!(nearest_brir_index, m)?)?;
    m.add_function(wrap_pyfunction!(gain_ramp, m)?)?;
    m.add_class::<BusMixer>()?;
    m.add_class::<RenderGrid>()?;
    Ok(())
}
