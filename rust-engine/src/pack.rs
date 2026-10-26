//! Baked 7.1 BRIR loader: reads the owner's per-speaker IR WAVs (`<channel>_L.wav` / `<channel>_R.wav`,
//! mono float/int) into the ring + LFE banks the renderer needs. The engine's own minimal reader
//! (hound), so the standalone binary loads BRIRs with no Python and no SOFA/HDF5 dependency — the
//! same `baked_7p1_test` directory the Python engine's `--brir-dir` consumes.

use std::path::Path;

use crate::geometry::{ring_indices, speaker_azimuth, CHANNELS_7P1, LFE_INDEX};

/// The loaded BRIR banks, laid out for [`render_core_lib::GridRender`].
pub struct BrirPack {
    pub samplerate: u32,
    pub n_taps: usize,
    /// Ring (directional) left/right IR banks, row-major `(n_ring, n_taps)`, in ring order.
    pub ring_left: Vec<f64>,
    pub ring_right: Vec<f64>,
    pub ring_az: Vec<f64>,
    /// Ring channels' column indices in the 8-ch 7.1 input.
    pub dir_idx: Vec<usize>,
    pub n_ring: usize,
    /// LFE IR (mono per ear) and its input column.
    pub lfe_left: Vec<f64>,
    pub lfe_right: Vec<f64>,
    pub lfe_idx: usize,
}

/// Read one mono IR WAV → f64 in [-1, 1] (int → /max, float as-is), channel 0 if multichannel.
fn read_ir(path: &Path) -> Result<(u32, Vec<f64>), String> {
    let mut r = hound::WavReader::open(path).map_err(|e| format!("{}: {e}", path.display()))?;
    let spec = r.spec();
    let ch = spec.channels.max(1) as usize;
    let take0 = |it: Vec<f64>| it.into_iter().step_by(ch).collect::<Vec<f64>>();
    let samples: Vec<f64> = match spec.sample_format {
        hound::SampleFormat::Float => {
            take0(r.samples::<f32>().map(|s| s.unwrap_or(0.0) as f64).collect())
        }
        hound::SampleFormat::Int => {
            let max = (1i64 << (spec.bits_per_sample - 1)) as f64;
            take0(r.samples::<i32>().map(|s| s.unwrap_or(0) as f64 / max).collect())
        }
    };
    // Reject a non-finite tap (NaN/Inf) at load: once a NaN enters the partitioned convolver's
    // persistent FFT state it poisons every subsequent output block forever (BUG-21).
    if let Some(i) = samples.iter().position(|v| !v.is_finite()) {
        return Err(format!("{}: non-finite IR tap at sample {i}", path.display()));
    }
    Ok((spec.sample_rate, samples))
}

impl BrirPack {
    /// Load the baked 7.1 set from `dir` (EqualizerAPO naming `<ch>_L.wav`/`<ch>_R.wav`). All IRs are
    /// zero-padded to the longest.
    pub fn load(dir: &Path) -> Result<Self, String> {
        let mut sr = 0u32;
        let mut left: Vec<Vec<f64>> = Vec::with_capacity(8);
        let mut right: Vec<Vec<f64>> = Vec::with_capacity(8);
        for ch in CHANNELS_7P1 {
            let (srl, l) = read_ir(&dir.join(format!("{ch}_L.wav")))?;
            let (srr, rr) = read_ir(&dir.join(format!("{ch}_R.wav")))?;
            if sr == 0 {
                sr = srl;
            }
            if srl != sr || srr != sr {
                return Err(format!("{ch}: sample-rate mismatch ({srl}/{srr} vs {sr})"));
            }
            left.push(l);
            right.push(rr);
        }
        let n_taps = left.iter().chain(right.iter()).map(|v| v.len()).max().unwrap_or(0);
        if n_taps == 0 {
            return Err("empty BRIR set".into());
        }
        let pad = |mut v: Vec<f64>| {
            v.resize(n_taps, 0.0);
            v
        };
        let left: Vec<Vec<f64>> = left.into_iter().map(pad).collect();
        let right: Vec<Vec<f64>> = right.into_iter().map(pad).collect();

        let dir_idx = ring_indices();
        let n_ring = dir_idx.len();
        let mut ring_left = Vec::with_capacity(n_ring * n_taps);
        let mut ring_right = Vec::with_capacity(n_ring * n_taps);
        let mut ring_az = Vec::with_capacity(n_ring);
        for &i in &dir_idx {
            ring_left.extend_from_slice(&left[i]);
            ring_right.extend_from_slice(&right[i]);
            ring_az.push(speaker_azimuth(CHANNELS_7P1[i]));
        }
        Ok(BrirPack {
            samplerate: sr,
            n_taps,
            ring_left,
            ring_right,
            ring_az,
            dir_idx,
            n_ring,
            lfe_left: left[LFE_INDEX].clone(),
            lfe_right: right[LFE_INDEX].clone(),
            lfe_idx: LFE_INDEX,
        })
    }
}
