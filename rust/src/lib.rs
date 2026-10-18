//! Native batched **non-uniform** partitioned-convolution render kernel (B8.8).
//!
//! The one heavy, GPU-resident piece of the world-lock passthrough is the batched
//! fixed-filter convolution of the `C` BRIR **buses** (see
//! `binaural_headtracker.gpu_render.GpuBusConvolver`): each bus, for each ear, is a
//! partitioned overlap-save convolution with a fixed BRIR, summed to stereo. The
//! routing/crossfade (`BusMixer`) that produces the bus signals stays in tested Python.
//!
//! B8.2 shipped this as a **uniformly**-partitioned overlap-save convolver (`P = ceil(N/B)`
//! equal partitions of `B` taps). For a ~24000-tap BRIR that is ~47 complex multiply-accumulate
//! passes per block — MAC-bound. B8.8 replaces it with **non-uniform partitioned convolution**
//! (NUPC, Gardner 1995 / García 2002): small partitions for the head, progressively larger
//! partitions for the tail (which update far less often), cutting per-block MAC throughput for
//! the long tail.
//!
//! ### Decomposition (exact)
//!
//! The filter `h` (length `N`) is split into contiguous **segments**; segment `i` occupies taps
//! `[a_i, a_i + len_i)`. Because convolution is linear,
//! `y = Σ_i delay(a_i, conv(h[a_i : a_i+len_i], x))`: each segment is an **independent**
//! uniformly-partitioned OLS convolver run at its own partition length `L_i` (a multiple of the
//! block `B`), whose output is delayed by `a_i` samples before being summed. This equals the
//! full linear convolution *regardless of the per-segment FFT sizes*, so the output stays
//! bit-identical to `scipy.fftconvolve` / the old uniform kernel to floating round-off (~1e-13).
//!
//! A segment run at partition length `L` collects `L/B` input blocks before it transforms, so it
//! produces output in `L`-sample super-blocks every `L/B` blocks. That is causal and in time for
//! the global `B`-block output iff `a_i >= L_i - B` — which the schedule guarantees (the head
//! covers enough taps before the first larger stage starts). Each segment's super-block output is
//! accumulated, delayed by `a_i`, into a per-ear output ring from which each block reads its `B`
//! samples.
//!
//! `BusConvolver::new(left, right, block_size)` takes the two `(C, n_taps)` BRIR banks and
//! `process(bus_in: (C, block)) -> (block, 2)` convolves one bus block to stereo, keeping every
//! segment's frequency-domain delay line resident between calls. Allocation-free per block, and
//! multithreaded across the buses with rayon. An optional `schedule` argument overrides the
//! built-in partition policy (used by the benchmarks); the default policy is a good balance of
//! MAC vs FFT cost for the real BRIR.

use numpy::ndarray::Array2;
use numpy::{IntoPyArray, PyArray2, PyReadonlyArray2};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use rayon::prelude::*;
use realfft::num_complex::Complex;
use realfft::{ComplexToReal, RealFftPlanner, RealToComplex};
use std::sync::Arc;

type C64 = Complex<f64>;

/// One partition-length stage shared across all bus×ear convolvers (specs + FFT planners).
struct Stage {
    l: usize,      // partition length (= block * period); multiple of block
    nfft: usize,   // 2*l
    f: usize,      // l + 1 (half-spectrum length)
    p: usize,      // number of partitions in this stage
    a: usize,      // filter offset / output delay (multiple of block); a >= l - block
    r2c: Arc<dyn RealToComplex<f64>>,
    c2r: Arc<dyn ComplexToReal<f64>>,
}

/// Per-bus, per-stage state. The input-side state (FDL, history, input FFT) is shared by both
/// ears; only the pre-transformed filter `hf_*` and the per-ear accumulate/inverse scratch differ.
struct BusStage {
    // input side (shared by both ears)
    fdl: Vec<C64>,       // p*f frequency-domain delay line (spectra of recent [prev,x] windows)
    prev: Vec<f64>,      // l  previous super-block input (overlap-save history)
    inbuf: Vec<f64>,     // l  accumulates B-sample pushes until a full super-block
    fill: usize,         // samples currently in inbuf
    pos: usize,          // newest FDL slot
    fired: usize,        // super-blocks emitted so far (for the output offset)
    window: Vec<f64>,    // 2l  r2c input ([prev, inbuf])
    xspec: Vec<C64>,     // f   input spectrum
    scratch_f: Vec<C64>, // forward FFT scratch
    // per ear
    hf_l: Vec<C64>,      // p*f pre-transformed left filter partitions
    hf_r: Vec<C64>,      // p*f pre-transformed right filter partitions
    acc: Vec<C64>,       // f   MAC accumulator (reused per ear)
    time: Vec<f64>,      // 2l  inverse FFT output
    scratch_i: Vec<C64>, // inverse FFT scratch
}

/// Per-bus container: one `BusStage` per stage plus the two delayed-output rings.
struct Bus {
    stages: Vec<BusStage>,
    ring_l: Vec<f64>, // length ring_len; delayed-and-summed left output timeline
    ring_r: Vec<f64>,
}

#[pyclass]
struct BusConvolver {
    block: usize,
    c: usize, // buses
    ring_len: usize,
    t_emit: usize, // output samples already emitted (== blocks * block)
    stages: Vec<Stage>,
    buses: Vec<Bus>,
}

/// Build the non-uniform partition schedule: a list of `(L, n_parts)` segments in filter order.
///
/// Policy: a head stage at the block size `B`, then tail stages whose partition length doubles,
/// each with `group` partitions, until the `n_taps`-tap filter is covered. `group` trades FFT
/// cost (fewer, bigger transforms when large) against MAC cost (more partitions per size). The
/// `a >= L - B` causality constraint always holds: after the head (`group` partitions, offset
/// `group*B >= B`) every later stage's offset already exceeds its length.
fn default_schedule(n_taps: usize, block: usize, group: usize) -> Vec<(usize, usize)> {
    let mut sched = Vec::new();
    let mut remaining = n_taps;
    let mut l = block;
    let mut a = 0usize;
    while remaining > 0 {
        // Partitions for this stage: `group`, but never more than needed to finish the filter.
        let want = group * l;
        let take = want.min(remaining);
        let nparts = take.div_ceil(l);
        sched.push((l, nparts));
        a += nparts * l;
        remaining = remaining.saturating_sub(nparts * l);
        if remaining > 0 {
            let next_l = l * 2;
            // Keep the causality constraint a >= next_l - block (always true here; assert-safe).
            if next_l <= a + block {
                l = next_l;
            }
        }
    }
    sched
}

#[pymethods]
impl BusConvolver {
    #[new]
    #[pyo3(signature = (left, right, block_size, schedule=None))]
    fn new(
        left: PyReadonlyArray2<f64>,
        right: PyReadonlyArray2<f64>,
        block_size: usize,
        schedule: Option<Vec<(usize, usize)>>,
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

        // Schedule as (L, n_parts). L defaults from a built-in policy (group=4 balances MAC/FFT
        // for the real BRIR); an explicit schedule (for benchmarks) must still cover the filter.
        let sched = match schedule {
            Some(s) => s,
            None => default_schedule(n_taps, block, 4),
        };

        let mut planner = RealFftPlanner::<f64>::new();

        // Build the shared stage specs (with offsets) and validate coverage + causality.
        let mut stages: Vec<Stage> = Vec::with_capacity(sched.len());
        let mut a = 0usize;
        for (seg_l, seg_p) in &sched {
            let seg_l = *seg_l;
            let seg_p = *seg_p;
            if seg_l == 0 || seg_p == 0 {
                return Err(PyValueError::new_err("schedule entries must be positive"));
            }
            if seg_l % block != 0 {
                return Err(PyValueError::new_err("each partition length must be a multiple of block"));
            }
            if a + block < seg_l {
                return Err(PyValueError::new_err(format!(
                    "schedule violates causality: offset {a} < L {seg_l} - block {block}"
                )));
            }
            let nfft = 2 * seg_l;
            stages.push(Stage {
                l: seg_l,
                nfft,
                f: seg_l + 1,
                p: seg_p,
                a,
                r2c: planner.plan_fft_forward(nfft),
                c2r: planner.plan_fft_inverse(nfft),
            });
            a += seg_p * seg_l;
        }
        if a < n_taps {
            return Err(PyValueError::new_err(format!(
                "schedule covers {a} taps < filter {n_taps}"
            )));
        }

        // Output ring must outrun the furthest-ahead contribution. A super-block that fires at the
        // current block adds into [t_emit, t_emit + a_s + block); size the ring past max(a)+max(L).
        let max_a = stages.iter().map(|s| s.a).max().unwrap_or(0);
        let max_l = stages.iter().map(|s| s.l).max().unwrap_or(block);
        let ring_len = ((max_a + max_l) / block + 2) * block;

        // Pre-transform each stage's filter partitions (per ear), mirroring the old kernel.
        let build_hf = |bank: &numpy::ndarray::ArrayView2<f64>, ch: usize, st: &Stage| -> Vec<C64> {
            let mut hf = vec![C64::new(0.0, 0.0); st.p * st.f];
            let mut pad = st.r2c.make_input_vec();
            let mut spec = st.r2c.make_output_vec();
            let mut scratch = st.r2c.make_scratch_vec();
            for part in 0..st.p {
                for v in pad.iter_mut() {
                    *v = 0.0;
                }
                let base = st.a + part * st.l;
                for i in 0..st.l {
                    let t = base + i;
                    if t < n_taps {
                        pad[i] = bank[[ch, t]];
                    }
                }
                st.r2c.process_with_scratch(&mut pad, &mut spec, &mut scratch).unwrap();
                hf[part * st.f..part * st.f + st.f].copy_from_slice(&spec);
            }
            hf
        };

        let buses: Vec<Bus> = (0..c)
            .map(|ch| {
                let stages_state: Vec<BusStage> = stages
                    .iter()
                    .map(|st| BusStage {
                        fdl: vec![C64::new(0.0, 0.0); st.p * st.f],
                        prev: vec![0.0; st.l],
                        inbuf: vec![0.0; st.l],
                        fill: 0,
                        pos: 0,
                        fired: 0,
                        window: st.r2c.make_input_vec(),
                        xspec: st.r2c.make_output_vec(),
                        scratch_f: st.r2c.make_scratch_vec(),
                        hf_l: build_hf(&l, ch, st),
                        hf_r: build_hf(&r, ch, st),
                        acc: st.c2r.make_input_vec(),
                        time: st.c2r.make_output_vec(),
                        scratch_i: st.c2r.make_scratch_vec(),
                    })
                    .collect();
                Bus {
                    stages: stages_state,
                    ring_l: vec![0.0; ring_len],
                    ring_r: vec![0.0; ring_len],
                }
            })
            .collect();

        Ok(Self { block, c, ring_len, t_emit: 0, stages, buses })
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
        let block = self.block;
        let ring_len = self.ring_len;
        let t_emit = self.t_emit;

        // Copy each bus's input row into its head/stage input buffers before the parallel section,
        // so the closure borrows only its own Bus (the PyReadonlyArray isn't Sync). Each stage
        // accumulates the same input; stash the block at the stage's current fill offset.
        for ch in 0..self.c {
            let bus = &mut self.buses[ch];
            for bs in bus.stages.iter_mut() {
                let fill = bs.fill;
                for i in 0..block {
                    bs.inbuf[fill + i] = x[[ch, i]];
                }
            }
        }

        let stages = &self.stages;
        let buses = &mut self.buses;

        // Release the GIL for the pure-Rust convolution (the input is already copied), so the
        // camera/pose loop runs concurrently with the render.
        py.allow_threads(|| {
            buses.par_iter_mut().for_each(|bus| {
                let (ring_l, ring_r) = (&mut bus.ring_l, &mut bus.ring_r);
                for (st, bs) in stages.iter().zip(bus.stages.iter_mut()) {
                    bs.fill += block;
                    if bs.fill < st.l {
                        continue; // super-block not full yet
                    }
                    // Full super-block: window = [prev, inbuf] (2L), transform, store as newest FDL.
                    bs.fill = 0;
                    bs.window[..st.l].copy_from_slice(&bs.prev);
                    bs.window[st.l..].copy_from_slice(&bs.inbuf);
                    bs.prev.copy_from_slice(&bs.inbuf);
                    st.r2c
                        .process_with_scratch(&mut bs.window, &mut bs.xspec, &mut bs.scratch_f)
                        .unwrap();
                    bs.fdl[bs.pos * st.f..bs.pos * st.f + st.f].copy_from_slice(&bs.xspec);

                    // This super-block's output lands at global times [start, start+L).
                    let start = bs.fired * st.l + st.a;
                    bs.fired += 1;
                    let inv_n = 1.0 / st.nfft as f64;

                    for ear in 0..2 {
                        let hf = if ear == 0 { &bs.hf_l } else { &bs.hf_r };
                        for v in bs.acc.iter_mut() {
                            *v = C64::new(0.0, 0.0);
                        }
                        // acc[k] = Σ_p Hf[p]·FDL[(pos - p) mod P]
                        for part in 0..st.p {
                            let fi = (bs.pos + st.p - part) % st.p;
                            let h = &hf[part * st.f..part * st.f + st.f];
                            let xd = &bs.fdl[fi * st.f..fi * st.f + st.f];
                            for k in 0..st.f {
                                bs.acc[k] += h[k] * xd[k];
                            }
                        }
                        bs.acc[0].im = 0.0;
                        bs.acc[st.f - 1].im = 0.0;
                        st.c2r
                            .process_with_scratch(&mut bs.acc, &mut bs.time, &mut bs.scratch_i)
                            .unwrap();
                        // Overlap-save: alias-free samples are the 2nd half; add (delayed by `a`,
                        // via `start`) into this ear's output ring.
                        let ring = if ear == 0 { &mut *ring_l } else { &mut *ring_r };
                        for n in 0..st.l {
                            let idx = (start + n) % ring_len;
                            ring[idx] += bs.time[st.l + n] * inv_n;
                        }
                    }
                    bs.pos = (bs.pos + 1) % st.p;
                }
            });
        });

        // Emit output block [t_emit, t_emit+block), zeroing the ring slots as they are consumed.
        let mut stereo = Array2::<f64>::zeros((block, 2));
        for bus in &mut self.buses {
            for n in 0..block {
                let idx = (t_emit + n) % ring_len;
                stereo[[n, 0]] += bus.ring_l[idx];
                stereo[[n, 1]] += bus.ring_r[idx];
            }
        }
        for bus in &mut self.buses {
            for n in 0..block {
                let idx = (t_emit + n) % ring_len;
                bus.ring_l[idx] = 0.0;
                bus.ring_r[idx] = 0.0;
            }
        }
        self.t_emit += block;
        Ok(stereo.into_pyarray_bound(py))
    }
}

#[pymodule]
fn binaural_kernel(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<BusConvolver>()?;
    Ok(())
}
