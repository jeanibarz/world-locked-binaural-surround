//! Head-rotation ring interpolation over the baked 7.1 BRIRs — the Rust port of the Python
//! reference `binaural_headtracker.baked_interp` + `live_render.interpolated_ring`, matching it to
//! floating error (a parity harness gates it against numpy/scipy on the owner's Audeze set).
//!
//! The engine steers a world-locked source over a ring of directional BRIRs. With only the 8 baked
//! speakers the ring is sparse and the source snaps between them; these methods synthesize the
//! in-between directions so a head turn moves the phantom smoothly:
//!
//! * `nearest`   — snap each grid point to the nearest measured BRIR (no synthesis).
//! * `timealign` — time-align the two bracketing directs, blend in time, re-apply the interpolated
//!   ITD; equal-power tail blend. (The owner's preferred method, and the engine default.)
//! * `minphase`  — min-phase reconstruction of the blended direct magnitude + interpolated ITD.
//! * `notchwarp` — slide the N1 pinna notch to the interpolated frequency, blended excess phase.
//!
//! The dense ring is precomputed once when a method (or step) is selected — the realtime render path
//! never runs any of this, so BUG-13's allocation-free hot loop is untouched.
//!
//! FFT conventions mirror numpy exactly: `rfft`/`irfft` via `realfft` (irfft divided by N, as numpy
//! normalises), full complex `fft`/`ifft` via `rustfft` (inverse divided by N). `estimate_delay`
//! uses the analytic (Hilbert) envelope peak, matching `scipy.signal.hilbert`.

use realfft::num_complex::Complex;
use realfft::RealFftPlanner;
use rustfft::FftPlanner;

type C64 = Complex<f64>;

const DIRECT_MS: f64 = 3.0;
const ONSET_SEARCH_MS: f64 = 10.0;
const EXACT_TOL_DEG: f64 = 1e-6;
const SMOOTH_FRAC_OCT: f64 = 1.0 / 3.0;
const LOUDNESS_CLAMP_DB: f64 = 9.0;
const NOTCH_BAND_HZ: (f64, f64) = (5000.0, 11000.0);

/// A dense directional BRIR ring: `naz` BRIRs (row-major `naz × n_taps`) at `az` degrees, wrapped to
/// (-180, 180] (the engine's steering convention).
pub struct DenseRing {
    pub left: Vec<f64>,
    pub right: Vec<f64>,
    pub az: Vec<f64>,
    pub naz: usize,
    pub n_taps: usize,
}

fn wrap_deg(a: f64) -> f64 {
    (a + 180.0).rem_euclid(360.0) - 180.0
}

// ---- FFT helpers (numpy-equivalent) ---------------------------------------------------------

/// `np.fft.rfft(x, n=nfft)` — zero-pad/truncate `x` to `nfft`, forward real FFT → `nfft/2+1` bins.
fn rfft(x: &[f64], nfft: usize) -> Vec<C64> {
    let mut planner = RealFftPlanner::<f64>::new();
    let r2c = planner.plan_fft_forward(nfft);
    let mut inp = r2c.make_input_vec();
    let m = x.len().min(nfft);
    inp[..m].copy_from_slice(&x[..m]);
    for v in inp[m..].iter_mut() {
        *v = 0.0;
    }
    let mut out = r2c.make_output_vec();
    r2c.process(&mut inp, &mut out).unwrap();
    out
}

/// `np.fft.irfft(spec, n=nfft)` — inverse real FFT (normalised by `nfft`, as numpy does).
fn irfft(spec: &[C64], nfft: usize) -> Vec<f64> {
    let mut planner = RealFftPlanner::<f64>::new();
    let c2r = planner.plan_fft_inverse(nfft);
    let mut inp = c2r.make_input_vec();
    inp.copy_from_slice(spec);
    // realfft requires the DC (and Nyquist, for even n) bin to be real.
    inp[0].im = 0.0;
    if nfft % 2 == 0 {
        let last = inp.len() - 1;
        inp[last].im = 0.0;
    }
    let mut out = c2r.make_output_vec();
    c2r.process(&mut inp, &mut out).unwrap();
    let inv = 1.0 / nfft as f64;
    for v in out.iter_mut() {
        *v *= inv;
    }
    out
}

/// `np.fft.fft` of a real signal → full complex spectrum (length `n`).
fn fft_real(x: &[f64]) -> Vec<C64> {
    let n = x.len();
    let mut planner = FftPlanner::<f64>::new();
    let fft = planner.plan_fft_forward(n);
    let mut buf: Vec<C64> = x.iter().map(|&v| C64::new(v, 0.0)).collect();
    fft.process(&mut buf);
    buf
}

/// `np.fft.ifft` → full complex inverse FFT (normalised by `n`).
fn ifft(buf: &[C64]) -> Vec<C64> {
    let n = buf.len();
    let mut planner = FftPlanner::<f64>::new();
    let inv = planner.plan_fft_inverse(n);
    let mut b = buf.to_vec();
    inv.process(&mut b);
    let s = 1.0 / n as f64;
    for v in b.iter_mut() {
        *v *= s;
    }
    b
}

/// Analytic-signal (Hilbert) envelope `|hilbert(x)|`, matching `scipy.signal.hilbert`.
fn hilbert_envelope(x: &[f64]) -> Vec<f64> {
    let n = x.len();
    if n == 0 {
        return Vec::new();
    }
    let mut spec = fft_real(x);
    // h: 1 at DC; 2 on the positive-frequency bins; 1 at Nyquist (even n); 0 on the negatives.
    let mut h = vec![0.0f64; n];
    if n % 2 == 0 {
        h[0] = 1.0;
        h[n / 2] = 1.0;
        for v in h.iter_mut().take(n / 2).skip(1) {
            *v = 2.0;
        }
    } else {
        h[0] = 1.0;
        for v in h.iter_mut().take((n + 1) / 2).skip(1) {
            *v = 2.0;
        }
    }
    for (s, &hk) in spec.iter_mut().zip(h.iter()) {
        *s *= hk;
    }
    ifft(&spec).iter().map(|c| c.norm()).collect()
}

// ---- primitives (interpolate.py) ------------------------------------------------------------

/// Broadband arrival delay (sub-sample) = analytic-envelope peak with parabolic refinement.
fn estimate_delay(ir: &[f64]) -> f64 {
    if !ir.iter().any(|&v| v != 0.0) {
        return 0.0;
    }
    let env = hilbert_envelope(ir);
    let mut peak = 0usize;
    let mut best = f64::NEG_INFINITY;
    for (i, &v) in env.iter().enumerate() {
        if v > best {
            best = v;
            peak = i;
        }
    }
    if peak > 0 && peak < env.len() - 1 {
        let (a, b, c) = (env[peak - 1], env[peak], env[peak + 1]);
        let denom = a - 2.0 * b + c;
        if denom != 0.0 {
            let offset = 0.5 * (a - c) / denom;
            if offset > -1.0 && offset < 1.0 {
                return peak as f64 + offset;
            }
        }
    }
    peak as f64
}

/// Delay `x` by (fractional, signed) `delay` samples via a linear-phase FFT ramp. Circular; the
/// caller pads so only zeros wrap. Output length == input length.
fn fractional_delay(x: &[f64], delay: f64) -> Vec<f64> {
    let n = x.len();
    if n == 0 || delay == 0.0 {
        return x.to_vec();
    }
    let mut spec = rfft(x, n);
    for (k, s) in spec.iter_mut().enumerate() {
        let f = k as f64 / n as f64; // cycles/sample in [0, 0.5]
        let ph = -2.0 * std::f64::consts::PI * f * delay;
        *s *= C64::new(ph.cos(), ph.sin());
    }
    if n % 2 == 0 {
        let last = spec.len() - 1;
        spec[last].im = 0.0; // drop the Nyquist phase (can't represent a fractional shift)
    }
    irfft(&spec, n)
}

/// Minimum-phase impulse (length `nfft`) with the given rfft magnitude (`nfft/2+1` bins), via the
/// real-cepstrum method.
fn min_phase_from_magnitude(mag_half: &[f64], nfft: usize) -> Vec<f64> {
    // full = concat(mag_half, mag_half[-2:0:-1]) → length nfft
    let mut full = vec![0.0f64; nfft];
    full[..mag_half.len()].copy_from_slice(mag_half);
    let mut j = mag_half.len();
    for k in (1..mag_half.len() - 1).rev() {
        full[j] = mag_half[k];
        j += 1;
    }
    let log_mag: Vec<f64> = full.iter().map(|&v| v.max(1e-12).ln()).collect();
    // cep = ifft(log_mag).real
    let log_c: Vec<C64> = log_mag.iter().map(|&v| C64::new(v, 0.0)).collect();
    let cep: Vec<f64> = ifft(&log_c).iter().map(|c| c.re).collect();
    // window the cepstrum onto the causal side
    let half = nfft / 2;
    let mut win = vec![0.0f64; nfft];
    win[0] = 1.0;
    for v in win.iter_mut().take(half).skip(1) {
        *v = 2.0;
    }
    win[half] = 1.0;
    let cw: Vec<f64> = cep.iter().zip(win.iter()).map(|(&c, &w)| c * w).collect();
    // mp_spectrum = exp(fft(cep*win)); return ifft(mp_spectrum).real
    let spec = fft_real(&cw);
    let mp_spec: Vec<C64> = spec.iter().map(|c| c.exp()).collect();
    ifft(&mp_spec).iter().map(|c| c.re).collect()
}

fn nfft_for(split: usize) -> usize {
    let target = (2 * split).max(256) as f64;
    1usize << (target.log2().ceil() as u32)
}

// ---- per-ear interpolation methods (baked_interp.py) ----------------------------------------

/// timealign: `_interp_ear`.
fn interp_ear_timealign(ir_lo: &[f64], ir_hi: &[f64], w_lo: f64, w_hi: f64, split: usize) -> Vec<f64> {
    let n = ir_lo.len();
    let split = split.min(n);
    let pad = split;
    let mut dlo = vec![0.0f64; split + pad];
    let mut dhi = vec![0.0f64; split + pad];
    dlo[..split].copy_from_slice(&ir_lo[..split]);
    dhi[..split].copy_from_slice(&ir_hi[..split]);
    let t_lo = estimate_delay(&dlo);
    let t_hi = estimate_delay(&dhi);
    let alo = fractional_delay(&dlo, -t_lo);
    let ahi = fractional_delay(&dhi, -t_hi);
    let aligned: Vec<f64> = alo.iter().zip(ahi.iter()).map(|(&a, &b)| w_lo * a + w_hi * b).collect();
    let direct = fractional_delay(&aligned, w_lo * t_lo + w_hi * t_hi);
    blend_tail(ir_lo, ir_hi, w_lo, w_hi, split, &direct[..split])
}

/// minphase: `_interp_ear_minphase`.
fn interp_ear_minphase(ir_lo: &[f64], ir_hi: &[f64], w_lo: f64, w_hi: f64, split: usize) -> Vec<f64> {
    let n = ir_lo.len();
    let split = split.min(n);
    let pad = split;
    let mut dlo = vec![0.0f64; split + pad];
    let mut dhi = vec![0.0f64; split + pad];
    dlo[..split].copy_from_slice(&ir_lo[..split]);
    dhi[..split].copy_from_slice(&ir_hi[..split]);
    let t_lo = estimate_delay(&dlo);
    let t_hi = estimate_delay(&dhi);
    let alo = fractional_delay(&dlo, -t_lo);
    let ahi = fractional_delay(&dhi, -t_hi);
    let nfft = nfft_for(split);
    let mlo: Vec<f64> = rfft(&alo, nfft).iter().map(|c| c.norm()).collect();
    let mhi: Vec<f64> = rfft(&ahi, nfft).iter().map(|c| c.norm()).collect();
    let mag: Vec<f64> = mlo
        .iter()
        .zip(mhi.iter())
        .map(|(&a, &b)| (w_lo * a.max(1e-12).ln() + w_hi * b.max(1e-12).ln()).exp())
        .collect();
    let mp = min_phase_from_magnitude(&mag, nfft);
    let tstar = w_lo * t_lo + w_hi * t_hi;
    let mut placed_in = vec![0.0f64; split + pad];
    placed_in[..split].copy_from_slice(&mp[..split]);
    let placed = fractional_delay(&placed_in, tstar);
    blend_tail(ir_lo, ir_hi, w_lo, w_hi, split, &placed[..split])
}

/// notchwarp: `_interp_ear_notchwarp`.
fn interp_ear_notchwarp(
    ir_lo: &[f64], ir_hi: &[f64], w_lo: f64, w_hi: f64, split: usize, fs: f64,
) -> Vec<f64> {
    let n = ir_lo.len();
    let split = split.min(n);
    let pad = split;
    let mut dlo = vec![0.0f64; split + pad];
    let mut dhi = vec![0.0f64; split + pad];
    dlo[..split].copy_from_slice(&ir_lo[..split]);
    dhi[..split].copy_from_slice(&ir_hi[..split]);
    let t_lo = estimate_delay(&dlo);
    let t_hi = estimate_delay(&dhi);
    let alo = fractional_delay(&dlo, -t_lo);
    let ahi = fractional_delay(&dhi, -t_hi);
    let nfft = nfft_for(split);
    let a_lo = rfft(&alo, nfft);
    let a_hi = rfft(&ahi, nfft);
    let mlo: Vec<f64> = a_lo.iter().map(|c| c.norm()).collect();
    let mhi: Vec<f64> = a_hi.iter().map(|c| c.norm()).collect();
    let nbins = mlo.len();
    let n_lo = notch_bin(&mlo, fs, nfft);
    let n_hi = notch_bin(&mhi, fs, nfft);

    let mag: Vec<f64> = if let (Some(nl), Some(nh)) = (n_lo, n_hi) {
        let n_star = w_lo * nl as f64 + w_hi * nh as f64;
        let wl = warp_mag(&mlo, nl as f64, n_star, nbins);
        let wh = warp_mag(&mhi, nh as f64, n_star, nbins);
        wl.iter()
            .zip(wh.iter())
            .map(|(&a, &b)| (w_lo * a.max(1e-12).ln() + w_hi * b.max(1e-12).ln()).exp())
            .collect()
    } else {
        mlo.iter()
            .zip(mhi.iter())
            .map(|(&a, &b)| (w_lo * a.max(1e-12).ln() + w_hi * b.max(1e-12).ln()).exp())
            .collect()
    };

    let mp_spec = rfft(&min_phase_from_magnitude(&mag, nfft), nfft);
    let e_lo = excess_phase(&a_lo, nfft);
    let e_hi = excess_phase(&a_hi, nfft);
    let mut excess: Vec<C64> = e_lo
        .iter()
        .zip(e_hi.iter())
        .map(|(&a, &b)| a * w_lo + b * w_hi)
        .collect();
    for e in excess.iter_mut() {
        *e /= e.norm() + 1e-12;
    }
    let recon: Vec<C64> = mp_spec.iter().zip(excess.iter()).map(|(&m, &e)| m * e).collect();
    let direct = irfft(&recon, nfft);
    let tstar = w_lo * t_lo + w_hi * t_hi;
    let mut placed_in = vec![0.0f64; split + pad];
    placed_in[..split].copy_from_slice(&direct[..split]);
    let placed = fractional_delay(&placed_in, tstar);
    blend_tail(ir_lo, ir_hi, w_lo, w_hi, split, &placed[..split])
}

/// Assemble the output: interpolated `direct` in `[0,split)`, equal-power tail blend after.
fn blend_tail(ir_lo: &[f64], ir_hi: &[f64], w_lo: f64, w_hi: f64, split: usize, direct: &[f64]) -> Vec<f64> {
    let n = ir_lo.len();
    let mut out = vec![0.0f64; n];
    out[..split].copy_from_slice(&direct[..split]);
    let (gl, gr) = (w_lo.sqrt(), w_hi.sqrt());
    for i in split..n {
        out[i] = gl * ir_lo[i] + gr * ir_hi[i];
    }
    out
}

/// Unit-magnitude excess (all-pass) phase of a one-sided spectrum: `spec / minphase(|spec|)`.
fn excess_phase(spec: &[C64], nfft: usize) -> Vec<C64> {
    let mag: Vec<f64> = spec.iter().map(|c| c.norm()).collect();
    let amp = rfft(&min_phase_from_magnitude(&mag, nfft), nfft);
    spec.iter()
        .zip(amp.iter())
        .map(|(&s, &a)| {
            let e = s / (a + C64::new(1e-12, 0.0));
            e / (e.norm() + 1e-12)
        })
        .collect()
}

// ---- notch detection + warp (baked_interp.py) -----------------------------------------------

/// `np.searchsorted(a, v)` (side="left"): first index i with a[i] >= v.
fn searchsorted(a: &[f64], v: f64) -> usize {
    a.partition_point(|&x| x < v)
}

fn rfftfreq(nfft: usize, fs: f64) -> Vec<f64> {
    (0..=nfft / 2).map(|k| k as f64 * fs / nfft as f64).collect()
}

/// Bin of the deepest pinna notch in the band after removing a 1/2-octave envelope, else `None`.
fn notch_bin(mag: &[f64], fs: f64, nfft: usize) -> Option<usize> {
    let freqs = rfftfreq(nfft, fs);
    let logmag: Vec<f64> = mag.iter().map(|&m| 20.0 * m.max(1e-9).log10()).collect();
    let mut env = vec![0.0f64; logmag.len()];
    for (k, &f) in freqs.iter().enumerate() {
        let lo = searchsorted(&freqs, f / 2f64.powf(0.25));
        let hi = searchsorted(&freqs, f * 2f64.powf(0.25));
        if hi >= lo {
            // mean of logmag[lo..=hi]
            let hi_c = hi.min(logmag.len() - 1);
            let slice = &logmag[lo..=hi_c];
            env[k] = slice.iter().sum::<f64>() / slice.len() as f64;
        } else {
            env[k] = logmag[k];
        }
    }
    let detail: Vec<f64> = logmag.iter().zip(env.iter()).map(|(&l, &e)| l - e).collect();
    let mut best_j: Option<usize> = None;
    let mut best_v = f64::INFINITY;
    for (k, &f) in freqs.iter().enumerate() {
        if f >= NOTCH_BAND_HZ.0 && f <= NOTCH_BAND_HZ.1 && detail[k] < best_v {
            best_v = detail[k];
            best_j = Some(k);
        }
    }
    match best_j {
        Some(j) if detail[j] < -3.0 => Some(j),
        _ => None,
    }
}

/// Frequency-warp `mag` so its notch bin `n_src` lands at `n_star` (piecewise-linear, anchored at 0
/// and the last bin), via `np.interp` twice. Matches the reference exactly.
fn warp_mag(mag: &[f64], n_src: f64, n_star: f64, nbins: usize) -> Vec<f64> {
    let last = (nbins - 1) as f64;
    let xp = [0.0, n_star, last];
    let fp = [0.0, n_src, last];
    // orig[k] = interp(k, xp, fp)
    let orig: Vec<f64> = (0..nbins).map(|k| np_interp(k as f64, &xp, &fp)).collect();
    // result[k] = interp(orig[k], arange(nbins), mag)
    let xs: Vec<f64> = (0..nbins).map(|k| k as f64).collect();
    orig.iter().map(|&o| np_interp(o, &xs, mag)).collect()
}

/// `np.interp(x, xp, fp)` for strictly-increasing `xp`: linear, clamped to the endpoints.
fn np_interp(x: f64, xp: &[f64], fp: &[f64]) -> f64 {
    if x <= xp[0] {
        return fp[0];
    }
    if x >= xp[xp.len() - 1] {
        return fp[fp.len() - 1];
    }
    // first index i with xp[i] > x (xp strictly increasing)
    let i = xp.partition_point(|&v| v <= x);
    let (x0, x1) = (xp[i - 1], xp[i]);
    let (y0, y1) = (fp[i - 1], fp[i]);
    if x1 == x0 {
        return y0;
    }
    y0 + (y1 - y0) * (x - x0) / (x1 - x0)
}

// ---- loudness match (B1.13) -----------------------------------------------------------------

fn octave_smooth(power: &[f64], freqs: &[f64], frac: f64) -> Vec<f64> {
    let mut cs = vec![0.0f64; power.len() + 1];
    for i in 0..power.len() {
        cs[i + 1] = cs[i] + power[i];
    }
    let f_lo = 2f64.powf(frac / 2.0);
    freqs
        .iter()
        .map(|&f| {
            let lo = searchsorted(freqs, f / f_lo);
            let hi = searchsorted(freqs, f * f_lo).max(lo + 1);
            let hi = hi.min(cs.len() - 1);
            (cs[hi] - cs[lo]) / (hi - lo).max(1) as f64
        })
        .collect()
}

#[allow(clippy::too_many_arguments)]
fn loudness_match(
    left: &[f64], right: &[f64],
    lo_l: &[f64], lo_r: &[f64], hi_l: &[f64], hi_r: &[f64],
    w_lo: f64, w_hi: f64, samplerate: u32,
) -> (Vec<f64>, Vec<f64>) {
    let n = [left.len(), right.len(), lo_l.len(), lo_r.len(), hi_l.len(), hi_r.len()]
        .into_iter()
        .max()
        .unwrap_or(0);
    let nfft = if n > 1 { 1usize << (usize_bit_length(n - 1)) } else { 2 };
    let freqs = rfftfreq(nfft, samplerate as f64);
    let hl = rfft(left, nfft);
    let hr = rfft(right, nfft);
    let power = |a: &[C64], b: &[C64]| -> Vec<f64> {
        a.iter().zip(b.iter()).map(|(&x, &y)| x.norm_sqr() + y.norm_sqr()).collect()
    };
    let syn = octave_smooth(&power(&hl, &hr), &freqs, SMOOTH_FRAC_OCT);
    let endpoint = |l: &[f64], r: &[f64]| -> Vec<f64> {
        octave_smooth(&power(&rfft(l, nfft), &rfft(r, nfft)), &freqs, SMOOTH_FRAC_OCT)
    };
    let e_lo = endpoint(lo_l, lo_r);
    let e_hi = endpoint(hi_l, hi_r);
    let lim = 10f64.powf(LOUDNESS_CLAMP_DB / 20.0);
    let mut gain: Vec<f64> = (0..syn.len())
        .map(|k| {
            let tgt = w_lo * e_lo[k] + w_hi * e_hi[k];
            ((tgt + 1e-20) / (syn[k] + 1e-20)).sqrt().clamp(1.0 / lim, lim)
        })
        .collect();
    if gain.len() > 1 {
        gain[0] = gain[1];
    }
    let apply = |h: &[C64], len: usize| -> Vec<f64> {
        let scaled: Vec<C64> = h.iter().zip(gain.iter()).map(|(&c, &g)| c * g).collect();
        let mut t = irfft(&scaled, nfft);
        t.truncate(len);
        t
    };
    (apply(&hl, left.len()), apply(&hr, right.len()))
}

/// `int(n).bit_length()` for a positive `n` — numpy's `(n-1).bit_length()`.
fn usize_bit_length(n: usize) -> u32 {
    (usize::BITS) - n.leading_zeros()
}

// ---- ring assembly --------------------------------------------------------------------------

struct RingSpk {
    az: f64, // % 360, in [0,360)
    li: usize,
}

/// Measured directional speakers sorted by azimuth in [0,360), as `(az, index)`.
fn ring_sorted(ring_az_deg: &[f64]) -> Vec<RingSpk> {
    let mut r: Vec<RingSpk> =
        ring_az_deg.iter().enumerate().map(|(i, &a)| RingSpk { az: a.rem_euclid(360.0), li: i }).collect();
    r.sort_by(|a, b| a.az.partial_cmp(&b.az).unwrap_or(std::cmp::Ordering::Equal));
    r
}

/// Two bracketing speakers (indices into the measured set) + fraction (0 at lo, 1 at hi), wrapping.
fn bracket(ring: &[RingSpk], az: f64) -> (usize, usize, f64) {
    let az = az.rem_euclid(360.0);
    let m = ring.len();
    for i in 0..m {
        let (lo_i, hi_i) = (i, (i + 1) % m);
        let (a_lo, a_hi) = (ring[lo_i].az, ring[hi_i].az);
        let span = (a_hi - a_lo).rem_euclid(360.0);
        let d = (az - a_lo).rem_euclid(360.0);
        if d <= span || (d - span).abs() < 1e-9 {
            let frac = if span == 0.0 { 0.0 } else { d / span };
            return (ring[lo_i].li, ring[hi_i].li, frac);
        }
    }
    (ring[0].li, ring[0].li, 0.0)
}

fn circ_dist(a: f64, b: f64) -> f64 {
    let d1 = (a - b).rem_euclid(360.0);
    let d2 = (b - a).rem_euclid(360.0);
    d1.min(d2)
}

fn row<'a>(bank: &'a [f64], i: usize, n_taps: usize) -> &'a [f64] {
    &bank[i * n_taps..(i + 1) * n_taps]
}

/// Interpolate one `(left, right)` BRIR at azimuth `az` (deg) — the port of
/// `baked_interp.interpolate_baked_brir`.
#[allow(clippy::too_many_arguments)]
fn interpolate_one(
    ring_left: &[f64], ring_right: &[f64], ring: &[RingSpk], n_taps: usize, samplerate: u32,
    az: f64, method: &str, loudness: bool,
) -> (Vec<f64>, Vec<f64>) {
    let az_n = az.rem_euclid(360.0);
    if method == "nearest" {
        let idx = ring
            .iter()
            .min_by(|a, b| circ_dist(az_n, a.az).partial_cmp(&circ_dist(az_n, b.az)).unwrap())
            .unwrap()
            .li;
        return (row(ring_left, idx, n_taps).to_vec(), row(ring_right, idx, n_taps).to_vec());
    }
    // exact measured hit → return unchanged
    for s in ring {
        if circ_dist(az_n, s.az) <= EXACT_TOL_DEG {
            return (row(ring_left, s.li, n_taps).to_vec(), row(ring_right, s.li, n_taps).to_vec());
        }
    }
    let (ch_lo, ch_hi, frac) = bracket(ring, az_n);
    let (w_lo, w_hi) = (1.0 - frac, frac);
    let (ll, rl) = (row(ring_left, ch_lo, n_taps), row(ring_right, ch_lo, n_taps));
    let (lh, rh) = (row(ring_left, ch_hi, n_taps), row(ring_right, ch_hi, n_taps));

    let early = ((ONSET_SEARCH_MS * samplerate as f64 / 1000.0).round() as usize).max(1);
    let e = early.min(n_taps);
    let onset = estimate_delay(&ll[..e])
        .min(estimate_delay(&rl[..e]))
        .min(estimate_delay(&lh[..e]))
        .min(estimate_delay(&rh[..e]));
    let split = (onset + DIRECT_MS * samplerate as f64 / 1000.0).round() as usize;

    let ear = |lo: &[f64], hi: &[f64]| -> Vec<f64> {
        match method {
            "minphase" => interp_ear_minphase(lo, hi, w_lo, w_hi, split),
            "notchwarp" => interp_ear_notchwarp(lo, hi, w_lo, w_hi, split, samplerate as f64),
            _ => interp_ear_timealign(lo, hi, w_lo, w_hi, split),
        }
    };
    let mut left = ear(ll, lh);
    let mut right = ear(rl, rh);
    if loudness {
        let (l2, r2) = loudness_match(&left, &right, ll, rl, lh, rh, w_lo, w_hi, samplerate);
        left = l2;
        right = r2;
    }
    (left, right)
}

/// Build the dense interpolated ring — the port of `live_render.interpolated_ring`. One BRIR every
/// `step_deg` around the full circle. `loudness_match` defaults on in the render path.
#[allow(clippy::too_many_arguments)]
pub fn interpolated_ring(
    ring_left: &[f64], ring_right: &[f64], ring_az_deg: &[f64],
    _n_ring: usize, n_taps: usize, samplerate: u32,
    method: &str, step_deg: f64, loudness: bool,
) -> DenseRing {
    assert!(step_deg > 0.0, "step_deg must be positive");
    let ring = ring_sorted(ring_az_deg);
    let naz = (360.0 / step_deg).ceil() as usize;
    let mut left = vec![0.0f64; naz * n_taps];
    let mut right = vec![0.0f64; naz * n_taps];
    let mut az = vec![0.0f64; naz];
    for i in 0..naz {
        let a = i as f64 * step_deg;
        let (l, r) = interpolate_one(ring_left, ring_right, &ring, n_taps, samplerate, a, method, loudness);
        let m = l.len().min(n_taps);
        left[i * n_taps..i * n_taps + m].copy_from_slice(&l[..m]);
        let mr = r.len().min(n_taps);
        right[i * n_taps..i * n_taps + mr].copy_from_slice(&r[..mr]);
        az[i] = wrap_deg(a);
    }
    DenseRing { left, right, az, naz, n_taps }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fractional_delay_integer_shift_is_a_shift() {
        let mut x = vec![0.0; 16];
        x[4] = 1.0;
        let y = fractional_delay(&x, 3.0);
        assert!((y[7] - 1.0).abs() < 1e-9, "delta moved to index 7");
    }

    #[test]
    fn estimate_delay_of_a_delta_is_its_index() {
        let mut x = vec![0.0; 64];
        x[20] = 1.0;
        assert!((estimate_delay(&x) - 20.0).abs() < 1e-6);
    }

    #[test]
    fn searchsorted_matches_numpy_left() {
        let a = [0.0, 1.0, 2.0, 3.0];
        assert_eq!(searchsorted(&a, 2.0), 2);
        assert_eq!(searchsorted(&a, 2.5), 3);
        assert_eq!(searchsorted(&a, -1.0), 0);
    }

    #[test]
    fn np_interp_clamps_and_lerps() {
        let xp = [0.0, 2.0, 4.0];
        let fp = [0.0, 10.0, 20.0];
        assert!((np_interp(1.0, &xp, &fp) - 5.0).abs() < 1e-12);
        assert!((np_interp(-1.0, &xp, &fp) - 0.0).abs() < 1e-12);
        assert!((np_interp(9.0, &xp, &fp) - 20.0).abs() < 1e-12);
    }

    // Parity gate vs the Python reference (scipy/numpy) on the owner's Audeze baked_7p1_test set.
    // `#[ignore]`d because it reads the owner's pack + the dumped reference; run explicitly:
    //   cargo test --release parity_vs_python_reference -- --ignored --nocapture
    fn read_ref(method: &str) -> (Vec<f64>, Vec<f64>, Vec<f64>, usize, usize) {
        let dir = "/tmp/claude-1000/-home-jean-git-world-locked-binaural-surround/0fe2dec5-053f-4191-ba13-7316e1077cc0/scratchpad";
        let bytes = std::fs::read(format!("{dir}/ref_{method}.bin")).unwrap();
        let naz = u32::from_le_bytes(bytes[0..4].try_into().unwrap()) as usize;
        let ntaps = u32::from_le_bytes(bytes[4..8].try_into().unwrap()) as usize;
        let rd = |off: usize, count: usize| -> Vec<f64> {
            (0..count)
                .map(|i| f64::from_le_bytes(bytes[off + i * 8..off + i * 8 + 8].try_into().unwrap()))
                .collect()
        };
        let n = naz * ntaps;
        let left = rd(8, n);
        let right = rd(8 + n * 8, n);
        let az = rd(8 + 2 * n * 8, naz);
        (left, right, az, naz, ntaps)
    }

    #[test]
    #[ignore]
    fn bench_dense_ring_render_cost() {
        use crate::pack::BrirPack;
        use binaural_conv::BusConvolverCore;
        use render_core_lib::{GridRender, Lfe};
        let pack =
            BrirPack::load(std::path::Path::new("/mnt/c/Users/ibarz/WLBS-Engine/packs/default")).unwrap();
        let block = 256usize;
        let sr = pack.samplerate as f64;
        let budget_ms = block as f64 / sr * 1000.0;
        for (label, step) in [("measured-7", 0.0), ("10deg", 10.0), ("15deg", 15.0), ("20deg", 20.0)] {
            let (dl, dr, daz, ng) = if step == 0.0 {
                (pack.ring_left.clone(), pack.ring_right.clone(), pack.ring_az.clone(), pack.n_ring)
            } else {
                let d = interpolated_ring(&pack.ring_left, &pack.ring_right, &pack.ring_az,
                    pack.n_ring, pack.n_taps, pack.samplerate, "timealign", step, true);
                (d.left, d.right, d.az, d.naz)
            };
            let lfe = Lfe::Brir {
                conv: BusConvolverCore::new(&pack.lfe_left, &pack.lfe_right, 1, pack.n_taps, block).unwrap(),
                idx: pack.lfe_idx,
            };
            let mut gr = GridRender::new(&dl, &dr, ng, pack.n_taps, daz,
                pack.dir_idx.clone(), 8, 960, block, lfe).unwrap();
            let pcm = vec![0.01f64; block * 8];
            let az = crate::geometry::head_relative_azimuths(15.0, 1.0);
            let mut out = vec![0.0f64; block * 2];
            for _ in 0..20 { gr.process_into(&pcm, &az, &mut out); } // warm
            let t0 = std::time::Instant::now();
            let iters = 200;
            for _ in 0..iters { gr.process_into(&pcm, &az, &mut out); }
            let per = t0.elapsed().as_secs_f64() / iters as f64 * 1000.0;
            eprintln!("RENDERCOST {label:>10}: buses={ng:>3}  {per:.3} ms/block  ({:.0}% of {budget_ms:.2} ms budget)", per / budget_ms * 100.0);
        }
    }

    #[test]
    #[ignore]
    fn parity_vs_python_reference() {
        use crate::pack::BrirPack;
        let pack =
            BrirPack::load(std::path::Path::new("/mnt/c/Users/ibarz/WLBS-Engine/packs/default")).unwrap();
        let max_abs = |a: &[f64], b: &[f64]| a.iter().zip(b).map(|(x, y)| (x - y).abs()).fold(0.0, f64::max);
        let peak = |a: &[f64]| a.iter().map(|v| v.abs()).fold(0.0, f64::max);
        let mut worst_time = 0.0f64;
        for method in ["nearest", "timealign", "minphase", "notchwarp"] {
            let dense = interpolated_ring(
                &pack.ring_left, &pack.ring_right, &pack.ring_az, pack.n_ring, pack.n_taps,
                pack.samplerate, method, 10.0, true,
            );
            let (rl, rr, raz, naz, ntaps) = read_ref(method);
            assert_eq!((naz, ntaps), (dense.naz, dense.n_taps), "{method} shape");
            let ml = max_abs(&dense.left, &rl);
            let mr = max_abs(&dense.right, &rr);
            let maz = max_abs(&dense.az, &raz);
            let pk = peak(&rl).max(peak(&rr)).max(1e-30);
            let rel = ml.max(mr) / pk;
            eprintln!(
                "PARITY {method:>9}: max|ΔL|={ml:.3e} max|ΔR|={mr:.3e} rel={rel:.3e} max|Δaz|={maz:.3e} (peak={pk:.3e})"
            );
            worst_time = worst_time.max(rel);
            assert!(maz < 1e-9, "{method} azimuth mismatch {maz}");
        }
        // The synthesis methods go through several FFT round-trips + Hilbert + cepstrum; allow a
        // small floating divergence from numpy/pocketfft but keep it tight.
        assert!(worst_time < 1e-3, "parity drift too large: rel={worst_time:.3e}");
    }
}
