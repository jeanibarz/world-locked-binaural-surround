//! Native batched partitioned-convolution render kernel (B8.2) — the PyO3 wrapper.
//!
//! The one heavy, GPU-resident piece of the world-lock passthrough is the batched fixed-filter
//! convolution of the `C` BRIR **buses** (see `binaural_headtracker.gpu_render.GpuBusConvolver`):
//! each bus, for each ear, is a uniformly-partitioned overlap-save convolution with a fixed BRIR,
//! summed to stereo. The routing/crossfade (`BusMixer`) that produces the bus signals stays in
//! tested Python.
//!
//! The compute now lives in the pure-Rust [`binaural_conv`] core (shared with `render_core`, so the
//! standalone engine convolves with no interpreter in the loop). This crate is the thin PyO3 shim:
//! it converts the numpy filter banks / bus blocks to/from the core's slice API and keeps the
//! frequency-domain delay line resident between calls — so a `RustWorldLocked7p1` built on it still
//! matches the Python `WorldLocked7p1` to floating error, exactly as before.
//!
//! `BusConvolver::new(left, right, block_size)` takes the two `(C, n_taps)` BRIR banks and
//! `process(bus_in: (C, block)) -> (block, 2)` convolves one bus block to stereo.

// pyo3 0.22 macro expansion trips clippy's `useless_conversion` on the generated result-coercion.
#![allow(clippy::useless_conversion)]

use binaural_conv::BusConvolverCore;
use numpy::ndarray::Array2;
use numpy::{IntoPyArray, PyArray2, PyReadonlyArray2};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

/// Copy a 2-D numpy view into a row-major `Vec` (`out[r*cols + c]`), matching the core's layout.
fn to_row_major(a: &numpy::ndarray::ArrayView2<f64>) -> Vec<f64> {
    let (rows, cols) = (a.shape()[0], a.shape()[1]);
    let mut v = Vec::with_capacity(rows * cols);
    for r in 0..rows {
        for c in 0..cols {
            v.push(a[[r, c]]);
        }
    }
    v
}

#[pyclass]
struct BusConvolver {
    core: BusConvolverCore,
    c: usize,
    block: usize,
}

#[pymethods]
impl BusConvolver {
    #[new]
    fn new(
        left: PyReadonlyArray2<f64>,
        right: PyReadonlyArray2<f64>,
        block_size: usize,
    ) -> PyResult<Self> {
        let l = left.as_array();
        let r = right.as_array();
        if l.shape() != r.shape() {
            return Err(PyValueError::new_err("left and right must have the same shape"));
        }
        let c = l.shape()[0];
        let n_taps = l.shape()[1];
        let core = BusConvolverCore::new(&to_row_major(&l), &to_row_major(&r), c, n_taps, block_size)
            .map_err(PyValueError::new_err)?;
        Ok(Self { core, c, block: block_size })
    }

    #[getter]
    fn block_size(&self) -> usize {
        self.block
    }

    /// Convolve one `(C, block)` bus block → `(block, 2)` stereo (summed over buses).
    fn process<'py>(
        &mut self,
        py: Python<'py>,
        bus_in: PyReadonlyArray2<f64>,
    ) -> PyResult<Bound<'py, PyArray2<f64>>> {
        let x = bus_in.as_array();
        if x.shape() != [self.c, self.block] {
            return Err(PyValueError::new_err(format!(
                "bus_in must be ({}, {}); got {:?}",
                self.c,
                self.block,
                x.shape()
            )));
        }
        let flat = to_row_major(&x);
        let mut out_l = vec![0.0; self.block];
        let mut out_r = vec![0.0; self.block];
        // Release the GIL for the pure-Rust convolution (the core touches no Python objects).
        py.allow_threads(|| self.core.process_into(&flat, &mut out_l, &mut out_r));
        let mut stereo = Array2::<f64>::zeros((self.block, 2));
        for n in 0..self.block {
            stereo[[n, 0]] = out_l[n];
            stereo[[n, 1]] = out_r[n];
        }
        Ok(stereo.into_pyarray_bound(py))
    }
}

#[pymodule]
fn binaural_kernel(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<BusConvolver>()?;
    Ok(())
}
