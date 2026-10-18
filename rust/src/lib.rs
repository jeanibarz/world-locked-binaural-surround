//! Native batched partitioned-convolution render kernel (B8.2).
//!
//! The one heavy, GPU-resident piece of the world-lock passthrough is the batched
//! fixed-filter convolution of the `C` BRIR **buses** (see
//! `binaural_headtracker.gpu_render.GpuBusConvolver`): each bus, for each ear, is a
//! uniformly-partitioned overlap-save convolution with a fixed BRIR, summed to stereo. The
//! routing/crossfade (`BusMixer`) that produces the bus signals stays in tested Python.
//!
//! This crate reimplements exactly that convolver on the CPU in native code —
//! allocation-free per block, multithreaded across the bus×ear batch with rayon — so the
//! render keeps up in real time *without a GPU*. The math mirrors the Python
//! `PartitionedConvolver` (verified `== scipy.fftconvolve`) bin for bin: a `2B` real FFT per
//! block, `P` complex multiply-accumulates against the pre-transformed filter partitions, a
//! `2B` inverse real FFT, and the overlap-save second half — so a `RustWorldLocked7p1`
//! built on it matches the Python `WorldLocked7p1` to floating error.
//!
//! `BusConvolver::new(left, right, block_size)` takes the two `(C, n_taps)` BRIR banks and
//! `process(bus_in: (C, block)) -> (block, 2)` convolves one bus block to stereo, keeping the
//! frequency-domain delay line resident between calls.

use numpy::ndarray::Array2;
use numpy::{IntoPyArray, PyArray2, PyReadonlyArray2};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use rayon::prelude::*;
use realfft::num_complex::Complex;
use realfft::{ComplexToReal, RealFftPlanner, RealToComplex};
use std::sync::Arc;

type C64 = Complex<f64>;

/// Per-bus state and scratch (one is processed per rayon task, so all buffers are private).
struct Bus {
    hf_l: Vec<C64>, // P*F pre-transformed left filter partitions
    hf_r: Vec<C64>, // P*F pre-transformed right filter partitions
    fdl: Vec<C64>,  // P*F frequency-domain delay line (spectra of recent [prev,x] windows)
    prev: Vec<f64>, // previous input block (B), the overlap-save history
    // scratch (reused every block — no per-block allocation)
    window: Vec<f64>,   // 2B
    xspec: Vec<C64>,    // F
    acc: Vec<C64>,      // F
    time: Vec<f64>,     // 2B (inverse-FFT output)
    out_l: Vec<f64>,    // B
    out_r: Vec<f64>,    // B
    scratch_f: Vec<C64>, // forward FFT scratch
    scratch_i: Vec<C64>, // inverse FFT scratch
}

#[pyclass]
struct BusConvolver {
    block: usize,
    nfft: usize,
    f: usize, // nfft/2 + 1
    p: usize, // partitions
    c: usize, // buses
    pos: usize,
    r2c: Arc<dyn RealToComplex<f64>>,
    c2r: Arc<dyn ComplexToReal<f64>>,
    buses: Vec<Bus>,
}

#[pymethods]
impl BusConvolver {
    #[new]
    fn new(
        left: PyReadonlyArray2<f64>,
        right: PyReadonlyArray2<f64>,
        block_size: usize,
    ) -> PyResult<Self> {
        if block_size == 0 {
            return Err(PyValueError::new_err("block_size must be positive"));
        }
        let l = left.as_array();
        let r = right.as_array();
        if l.shape() != r.shape() {
            return Err(PyValueError::new_err("left and right must have the same shape"));
        }
        let c = l.shape()[0];
        let n_taps = l.shape()[1];
        if c == 0 || n_taps == 0 {
            return Err(PyValueError::new_err("need at least one channel and tap"));
        }
        let block = block_size;
        let nfft = 2 * block;
        let f = block + 1;
        let p = n_taps.div_ceil(block);

        let mut planner = RealFftPlanner::<f64>::new();
        let r2c = planner.plan_fft_forward(nfft);
        let c2r = planner.plan_fft_inverse(nfft);

        // Pre-transform each filter partition (B taps, zero-padded to 2B) to the half-spectrum,
        // exactly as the Python PartitionedConvolver precomputes `H`.
        let build_hf = |bank: &numpy::ndarray::ArrayView2<f64>| -> Vec<C64> {
            let mut hf = vec![C64::new(0.0, 0.0); c * p * f];
            let mut pad = r2c.make_input_vec();
            let mut spec = r2c.make_output_vec();
            let mut scratch = r2c.make_scratch_vec();
            for ch in 0..c {
                for part in 0..p {
                    for v in pad.iter_mut() {
                        *v = 0.0;
                    }
                    let base = part * block;
                    for i in 0..block {
                        let t = base + i;
                        if t < n_taps {
                            pad[i] = bank[[ch, t]];
                        }
                    }
                    r2c.process_with_scratch(&mut pad, &mut spec, &mut scratch).unwrap();
                    let off = (ch * p + part) * f;
                    hf[off..off + f].copy_from_slice(&spec);
                }
            }
            hf
        };
        let hf_l_all = build_hf(&l);
        let hf_r_all = build_hf(&r);

        let buses: Vec<Bus> = (0..c)
            .map(|ch| {
                let off = ch * p * f;
                Bus {
                    hf_l: hf_l_all[off..off + p * f].to_vec(),
                    hf_r: hf_r_all[off..off + p * f].to_vec(),
                    fdl: vec![C64::new(0.0, 0.0); p * f],
                    prev: vec![0.0; block],
                    window: r2c.make_input_vec(),
                    xspec: r2c.make_output_vec(),
                    acc: c2r.make_input_vec(),
                    time: c2r.make_output_vec(),
                    out_l: vec![0.0; block],
                    out_r: vec![0.0; block],
                    scratch_f: r2c.make_scratch_vec(),
                    scratch_i: c2r.make_scratch_vec(),
                }
            })
            .collect();

        Ok(Self { block, nfft, f, p, c, pos: 0, r2c, c2r, buses })
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
        // Copy the input rows into each bus's window history before the parallel section, so the
        // closure borrows only its own Bus (the PyReadonlyArray isn't Sync).
        for ch in 0..self.c {
            let b = &mut self.buses[ch];
            b.window[..self.block].copy_from_slice(&b.prev);
            for i in 0..self.block {
                let v = x[[ch, i]];
                b.window[self.block + i] = v;
                b.prev[i] = v;
            }
        }

        let (block, _nfft, f, p, pos) = (self.block, self.nfft, self.f, self.p, self.pos);
        let inv_n = 1.0 / self.nfft as f64;
        let r2c = &self.r2c;
        let c2r = &self.c2r;
        let buses = &mut self.buses;

        // Release the GIL for the pure-Rust convolution: the closure touches no Python
        // objects (the input was already copied into each bus above), so other Python
        // threads — e.g. the camera/pose loop — run concurrently with the render.
        py.allow_threads(|| {
        buses.par_iter_mut().for_each(|b| {
            // Forward FFT of this block's [prev, x] window → store as the newest FDL entry.
            r2c.process_with_scratch(&mut b.window, &mut b.xspec, &mut b.scratch_f)
                .unwrap();
            b.fdl[pos * f..pos * f + f].copy_from_slice(&b.xspec);

            for ear in 0..2 {
                let hf = if ear == 0 { &b.hf_l } else { &b.hf_r };
                // acc[freq] = Σ_p Hf[p] · FDL[(pos - p) mod P]  (newest window × partition 0)
                for v in b.acc.iter_mut() {
                    *v = C64::new(0.0, 0.0);
                }
                for part in 0..p {
                    let fi = (pos + p - part) % p;
                    let h = &hf[part * f..part * f + f];
                    let xd = &b.fdl[fi * f..fi * f + f];
                    for k in 0..f {
                        b.acc[k] += h[k] * xd[k];
                    }
                }
                // DC and Nyquist bins must be real for the inverse real FFT.
                b.acc[0].im = 0.0;
                b.acc[f - 1].im = 0.0;
                c2r.process_with_scratch(&mut b.acc, &mut b.time, &mut b.scratch_i)
                    .unwrap();
                // Overlap-save: the alias-free samples are the second half; scale (realfft's
                // inverse is unnormalised, numpy's irfft divides by nfft).
                let dst = if ear == 0 { &mut b.out_l } else { &mut b.out_r };
                for n in 0..block {
                    dst[n] = b.time[block + n] * inv_n;
                }
            }
        });
        });

        self.pos = (pos + 1) % p;

        // Sum the buses into interleaved stereo.
        let mut stereo = Array2::<f64>::zeros((block, 2));
        for b in &self.buses {
            for n in 0..block {
                stereo[[n, 0]] += b.out_l[n];
                stereo[[n, 1]] += b.out_r[n];
            }
        }
        Ok(stereo.into_pyarray_bound(py))
    }
}

#[pymodule]
fn binaural_kernel(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<BusConvolver>()?;
    Ok(())
}
