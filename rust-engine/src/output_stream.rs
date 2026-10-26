//! Output interpolation and recovery used by the Windows audio thread and portable tests.
//!
//! The FIFO decides when to rebuild its safety margin after a starvation. This stage keeps the
//! device clock running through that silence, fades its edges, and adjusts the resampling ratio
//! only while actual rendered blocks are available.

use audio_io::{DriftController, Pull, MAX_RESAMPLE_RATIO_RELATIVE};
use rubato::{FastFixedIn, PolynomialDegree, Resampler};

pub struct OutputResampler {
    nominal_ratio: f64,
    block: usize,
    cushion: usize,
    resampler: Option<FastFixedIn<f32>>,
    ctrl: DriftController,
    in_l: Vec<f32>,
    in_r: Vec<f32>,
    out_l: Vec<f32>,
    out_r: Vec<f32>,
    last_silent: bool,
    last_l: f32,
    last_r: f32,
    last_rel: f64,
}

impl OutputResampler {
    pub fn new(nominal_ratio: f64) -> Self {
        Self {
            nominal_ratio,
            block: 0,
            cushion: 0,
            resampler: None,
            ctrl: DriftController::new(1.0),
            in_l: Vec::new(),
            in_r: Vec::new(),
            out_l: Vec::new(),
            out_r: Vec::new(),
            last_silent: false,
            last_l: 0.0,
            last_r: 0.0,
            last_rel: 1.0,
        }
    }

    pub fn block_frames(&self) -> usize {
        self.block
    }
    pub fn correction_ppm(&self) -> f64 {
        self.ctrl.correction_ppm()
    }

    /// Append endpoint-rate stereo samples for one FIFO pull. `fill_blocks` is the queue depth
    /// immediately before that pull, and `cushion` is its current target depth.
    pub fn process_into(
        &mut self,
        pull: Pull,
        blk: &mut [f32],
        fill_blocks: usize,
        cushion: usize,
        source_idle: bool,
        pending: &mut Vec<f32>,
    ) -> Result<(), String> {
        let frames = blk.len() / 2;
        if frames == 0 || blk.len() % 2 != 0 {
            return Err("output block must contain complete stereo frames".into());
        }
        if self.resampler.is_none() || frames != self.block {
            self.block = frames;
            let r = FastFixedIn::<f32>::new(
                self.nominal_ratio,
                MAX_RESAMPLE_RATIO_RELATIVE,
                PolynomialDegree::Cubic,
                self.block,
                2,
            )
            .map_err(|e| {
                format!(
                    "output resampler init ({:.6}×, block {}): {e}",
                    self.nominal_ratio, self.block
                )
            })?;
            let omax = r.output_frames_max();
            self.resampler = Some(r);
            self.in_l.resize(self.block, 0.0);
            self.in_r.resize(self.block, 0.0);
            self.out_l.resize(omax, 0.0);
            self.out_r.resize(omax, 0.0);
            self.cushion = 0;
        }
        if cushion != self.cushion {
            self.cushion = cushion;
            self.ctrl = DriftController::new((cushion.max(1) * self.block) as f64);
        }
        let is_silent = pull != Pull::Block;
        let fade = 64.min(frames);
        if is_silent && !self.last_silent {
            for i in 0..fade {
                let g = 1.0 - i as f32 / fade as f32;
                blk[i * 2] = self.last_l * g;
                blk[i * 2 + 1] = self.last_r * g;
            }
        } else if !is_silent && self.last_silent {
            for i in 0..fade {
                let g = i as f32 / fade as f32;
                blk[i * 2] *= g;
                blk[i * 2 + 1] *= g;
            }
        }
        if !is_silent {
            self.last_l = blk[(frames - 1) * 2];
            self.last_r = blk[(frames - 1) * 2 + 1];
        }
        self.last_silent = is_silent;
        let rel = if pull == Pull::Block && !source_idle {
            self.ctrl.update((fill_blocks * self.block) as f64)
        } else {
            self.last_rel
        };
        self.last_rel = rel;
        let resampler = self.resampler.as_mut().unwrap();
        resampler
            .set_resample_ratio_relative(rel, true)
            .map_err(|e| format!("set resample ratio {rel:.6}: {e}"))?;
        for i in 0..self.block {
            self.in_l[i] = blk[i * 2];
            self.in_r[i] = blk[i * 2 + 1];
        }
        let written = resampler
            .process_into_buffer(
                &[&self.in_l[..self.block], &self.in_r[..self.block]],
                &mut [&mut self.out_l[..], &mut self.out_r[..]],
                None,
            )
            .map_err(|e| format!("resample: {e}"))?
            .1;
        pending.reserve(written * 2);
        for i in 0..written {
            pending.push(self.out_l[i]);
            pending.push(self.out_r[i]);
        }
        Ok(())
    }
}

#[cfg(test)]
#[path = "output_stream_tests.rs"]
mod tests;
