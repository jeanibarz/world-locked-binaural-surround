//! Pure-Rust batched partitioned (overlap-save) convolution core — the one heavy piece of the
//! world-lock render, with **no Python in the loop**.
//!
//! This is the compute extracted verbatim from the `binaural_kernel` PyO3 crate (B8.2): each of `C`
//! BRIR **buses**, for each ear, is a uniformly-partitioned overlap-save convolution with a fixed
//! BRIR, summed to stereo. A `2B` real FFT per block, `P` complex multiply-accumulates against the
//! pre-transformed filter partitions, a `2B` inverse real FFT, and the overlap-save second half —
//! the same math the Python `PartitionedConvolver` runs (verified `== scipy.fftconvolve`), bin for
//! bin. Allocation-free per block, multithreaded across the bus×ear batch with rayon.
//!
//! The API is slice-based (no numpy / no PyO3) so it links into both the `binaural_kernel` wheel and
//! the `render_core` Rust render. Filter banks and bus blocks are passed row-major: `bank[ch*n_taps
//! + t]`, `bus_in[ch*block + n]`.

// The convolution is heavily index-arithmetic (partition offsets, overlap-save halves); explicit
// range loops read closer to the Python reference than iterator adapters would.
#![allow(clippy::needless_range_loop)]

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
    window: Vec<f64>,    // 2B
    xspec: Vec<C64>,     // F
    acc: Vec<C64>,       // F
    time: Vec<f64>,      // 2B (inverse-FFT output)
    out_l: Vec<f64>,     // B
    out_r: Vec<f64>,     // B
    scratch_f: Vec<C64>, // forward FFT scratch
    scratch_i: Vec<C64>, // inverse FFT scratch
}

/// Batched partitioned convolver over `C` buses → stereo, state resident between blocks.
pub struct BusConvolverCore {
    block: usize,
    f: usize, // nfft/2 + 1
    p: usize, // partitions
    c: usize, // buses
    pos: usize,
    inv_n: f64,
    r2c: Arc<dyn RealToComplex<f64>>,
    c2r: Arc<dyn ComplexToReal<f64>>,
    buses: Vec<Bus>,
}

impl BusConvolverCore {
    /// Build from the two `(C, n_taps)` BRIR banks (row-major, `bank[ch*n_taps + t]`) and a block
    /// size. `left`/`right` must each be `c*n_taps` long.
    pub fn new(left: &[f64], right: &[f64], c: usize, n_taps: usize, block: usize) -> Result<Self, String> {
        if block == 0 {
            return Err("block_size must be positive".into());
        }
        if c == 0 || n_taps == 0 {
            return Err("need at least one channel and tap".into());
        }
        if left.len() != c * n_taps || right.len() != c * n_taps {
            return Err("left/right must be c*n_taps long".into());
        }
        let nfft = 2 * block;
        let f = block + 1;
        let p = n_taps.div_ceil(block);

        let mut planner = RealFftPlanner::<f64>::new();
        let r2c = planner.plan_fft_forward(nfft);
        let c2r = planner.plan_fft_inverse(nfft);

        // Pre-transform each filter partition (B taps, zero-padded to 2B) to the half-spectrum,
        // exactly as the Python PartitionedConvolver precomputes `H`.
        let build_hf = |bank: &[f64]| -> Vec<C64> {
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
                            pad[i] = bank[ch * n_taps + t];
                        }
                    }
                    r2c.process_with_scratch(&mut pad, &mut spec, &mut scratch).unwrap();
                    let off = (ch * p + part) * f;
                    hf[off..off + f].copy_from_slice(&spec);
                }
            }
            hf
        };
        let hf_l_all = build_hf(left);
        let hf_r_all = build_hf(right);

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

        Ok(Self { block, f, p, c, pos: 0, inv_n: 1.0 / nfft as f64, r2c, c2r, buses })
    }

    pub fn block_size(&self) -> usize {
        self.block
    }
    pub fn n_buses(&self) -> usize {
        self.c
    }

    /// Convolve one `(C, block)` bus block (row-major `bus_in[ch*block + n]`) → summed stereo,
    /// written into `out_l`/`out_r` (each length `block`, **overwritten**). Frequency-domain delay
    /// line advances by one block.
    pub fn process_into(&mut self, bus_in: &[f64], out_l: &mut [f64], out_r: &mut [f64]) {
        assert_eq!(bus_in.len(), self.c * self.block, "bus_in must be c*block");
        assert_eq!(out_l.len(), self.block);
        assert_eq!(out_r.len(), self.block);
        let block = self.block;
        // Load this block's [prev, x] window per bus (sequential; the parallel section is pure).
        for ch in 0..self.c {
            let b = &mut self.buses[ch];
            b.window[..block].copy_from_slice(&b.prev);
            for i in 0..block {
                let v = bus_in[ch * block + i];
                b.window[block + i] = v;
                b.prev[i] = v;
            }
        }

        let (f, p, pos, inv_n) = (self.f, self.p, self.pos, self.inv_n);
        let r2c = &self.r2c;
        let c2r = &self.c2r;
        self.buses.par_iter_mut().for_each(|b| {
            r2c.process_with_scratch(&mut b.window, &mut b.xspec, &mut b.scratch_f).unwrap();
            b.fdl[pos * f..pos * f + f].copy_from_slice(&b.xspec);
            for ear in 0..2 {
                let hf = if ear == 0 { &b.hf_l } else { &b.hf_r };
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
                b.acc[0].im = 0.0;
                b.acc[f - 1].im = 0.0;
                c2r.process_with_scratch(&mut b.acc, &mut b.time, &mut b.scratch_i).unwrap();
                let dst = if ear == 0 { &mut b.out_l } else { &mut b.out_r };
                for n in 0..block {
                    dst[n] = b.time[block + n] * inv_n;
                }
            }
        });
        self.pos = (pos + 1) % p;

        // Sum the buses into stereo (ascending bus order, matching the Python reference).
        for v in out_l.iter_mut() {
            *v = 0.0;
        }
        for v in out_r.iter_mut() {
            *v = 0.0;
        }
        for b in &self.buses {
            for n in 0..block {
                out_l[n] += b.out_l[n];
                out_r[n] += b.out_r[n];
            }
        }
    }

    /// Convenience: convolve and return freshly-allocated `(out_l, out_r)`.
    pub fn process(&mut self, bus_in: &[f64]) -> (Vec<f64>, Vec<f64>) {
        let mut l = vec![0.0; self.block];
        let mut r = vec![0.0; self.block];
        self.process_into(bus_in, &mut l, &mut r);
        (l, r)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unit_impulse_passes_filter_through() {
        // One bus, left IR = [0,1,0,...] (1-sample delay), right IR = delta. A unit impulse at the
        // first sample of block 0 should appear delayed by 1 in the left ear and at 0 in the right.
        let block = 4;
        let n_taps = 4;
        let left = vec![0.0, 1.0, 0.0, 0.0]; // delay by 1
        let right = vec![1.0, 0.0, 0.0, 0.0]; // delta
        let mut c = BusConvolverCore::new(&left, &right, 1, n_taps, block).unwrap();
        let mut bus_in = vec![0.0; block];
        bus_in[0] = 1.0;
        let (l, r) = c.process(&bus_in);
        assert!((r[0] - 1.0).abs() < 1e-12, "right delta at 0");
        assert!((l[1] - 1.0).abs() < 1e-12, "left delayed by 1");
        assert!(l[0].abs() < 1e-12 && r[1].abs() < 1e-12);
    }

    #[test]
    fn two_buses_sum() {
        let block = 4;
        let mut c = BusConvolverCore::new(
            &[1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0],
            &[1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0],
            2,
            4,
            block,
        )
        .unwrap();
        // bus0 impulse 2.0 at n=0, bus1 impulse 3.0 at n=0 -> summed 5.0 at n=0 each ear.
        let bus_in = vec![2.0, 0.0, 0.0, 0.0, 3.0, 0.0, 0.0, 0.0];
        let (l, r) = c.process(&bus_in);
        assert!((l[0] - 5.0).abs() < 1e-12 && (r[0] - 5.0).abs() < 1e-12);
    }
}
