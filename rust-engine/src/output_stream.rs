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
mod tests {
    use super::*;
    use audio_io::{
        capture::{read_available, PacketSource},
        cushion_blocks, CaptureFlags, CushionFifo,
    };
    use std::collections::VecDeque;
    use render_core_lib::{GridRender, Lfe};

    /// A finite device packet ring and auto-reset notification. Packet arrivals coalesce into a
    /// single signal, and a stalled client loses the oldest packets when the ring fills.
    #[derive(Default)]
    struct CaptureDevice {
        packets: VecDeque<Vec<f32>>,
        signalled: bool,
        lost_packets: usize,
        discontinuity: bool,
    }
    impl CaptureDevice {
        fn push(&mut self, packet: Vec<f32>) {
            if self.packets.len() == 6 {
                self.packets.pop_front();
                self.lost_packets += 1;
                self.discontinuity = true;
            }
            self.packets.push_back(packet);
            self.signalled = true;
        }
    }
    impl PacketSource for CaptureDevice {
        fn pending_frames(&self) -> Result<usize, String> {
            Ok(self.packets.front().map_or(0, |p| p.len() / 8))
        }
        fn wait(&mut self, _: u32) -> Result<bool, String> {
            Ok(std::mem::take(&mut self.signalled))
        }
        fn read_packet(&mut self, out: &mut Vec<f32>) -> Result<CaptureFlags, String> {
            out.extend(self.packets.pop_front().ok_or("empty simulated capture")?);
            Ok(CaptureFlags {
                silent: false,
                discontinuity: std::mem::take(&mut self.discontinuity),
            })
        }
    }

    /// Virtual devices have separate clocks: capture delivers 480-frame packets every 10 ms,
    /// while output requests 480 frames on its own clock. The render worker accumulates those
    /// packets into the selected block size; device periods deliberately do not equal DSP blocks.
    /// The device clocks and short test room filters are simulated. Capture draining, convolution, FIFO re-prime, output
    /// resampling, ratio smoothing, silence fades, and pending-output accounting are production code.
    struct Rig {
        fifo: CushionFifo<Vec<f32>>,
        stream: OutputResampler,
        renderer: GridRender,
        captured: VecDeque<f32>,
        device: CaptureDevice,
        pending: Vec<f32>,
        block: usize,
        source_frame: usize,
        max_pending: usize,
        measured_frames: usize,
        measured_energy: f64,
    }

    impl Rig {
        fn new() -> Self {
            let mut rig = Self {
                fifo: CushionFifo::new(4, 8, vec![0.0; 1024]),
                stream: OutputResampler::new(1.0),
                renderer: Self::renderer(512),
                captured: VecDeque::new(),
                device: CaptureDevice::default(),
                pending: Vec::new(),
                block: 512,
                source_frame: 0,
                max_pending: 0,
                measured_frames: 0,
                measured_energy: 0.0,
            };
            rig.reconfigure(512);
            rig
        }
        fn renderer(block: usize) -> GridRender {
            // Eight input channels and eight persistent convolution buses, with different ear
            // routes and a delayed reflection crossing partition boundaries at block 256.
            let mut left = vec![0.0; 8 * 1024];
            let mut right = left.clone();
            left[0] = 1.0;
            left[511] = 0.05;
            right[1024] = 1.0;
            right[1024 + 511] = 0.05;
            GridRender::new(&left, &right, 8, 1024,
                vec![30.0, -30.0, 0.0, 180.0, 90.0, -90.0, 150.0, -150.0],
                (0..8).collect(), 8, 64, block, Lfe::None).unwrap()
        }
        fn reconfigure(&mut self, block: usize) {
            self.block = block;
            self.renderer = Self::renderer(block);
            let cushion = cushion_blocks(40.0, 48_000, block);
            self.fifo = CushionFifo::new(cushion, cushion * 2, vec![0.0; block * 2]);
            // The engine clears its partial input on reconfiguration, but keeps endpoint-rate
            // pending samples and lets the next FIFO payload trigger the resampler's rebuild.
            self.captured.clear();
        }
        fn capture_packet(&mut self) {
            let mut packet = Vec::with_capacity(480 * 8);
            for _ in 0..480 {
                let sample = (self.source_frame as f32 * std::f32::consts::TAU * 440.0 / 48_000.0)
                    .sin()
                    * 0.1;
                packet.push(sample);
                packet.push(-sample);
                packet.extend([0.0; 6]);
                self.source_frame += 1;
            }
            self.device.push(packet);
        }
        fn render_available(&mut self, legacy_one_packet: bool) {
            let mut batch = Vec::new();
            if legacy_one_packet {
                // Negative control: exactly the old production wait-once/read-once policy.
                if self.device.wait(50).unwrap() && self.device.pending_frames().unwrap() > 0 {
                    self.device.read_packet(&mut batch).unwrap();
                }
            } else {
                read_available(&mut self.device, &mut batch, 50).unwrap();
            }
            self.captured.extend(batch);
            while self.captured.len() >= self.block * 8 {
                let input: Vec<f64> = self.captured.drain(..self.block * 8).map(|x| x as f64).collect();
                let mut output = vec![0.0; self.block * 2];
                self.renderer.process_into(&input,
                    &[30.0, -30.0, 0.0, 180.0, 90.0, -90.0, 150.0, -150.0], &mut output);
                self.fifo.push(output.into_iter().map(|x| x as f32).collect());
            }
        }
        fn output_event(&mut self, measure: bool) {
            while self.pending.len() < 480 * 2 {
                let fill = self.fifo.len();
                let (pull, block) = self.fifo.pull();
                self.stream
                    .process_into(
                        pull,
                        &mut block.unwrap(),
                        fill,
                        self.fifo.cushion(),
                        false,
                        &mut self.pending,
                    )
                    .unwrap();
                assert!(self.stream.correction_ppm().abs() <= 4000.00001);
                // A failure to produce output must terminate the test instead of spinning forever.
                assert!(self.pending.len() > 0);
            }
            self.max_pending = self.max_pending.max(self.pending.len() / 2);
            for &sample in &self.pending[..480 * 2] {
                assert!(
                    sample.is_finite() && sample.abs() <= 0.2,
                    "unsafe output sample {sample}"
                );
                if measure {
                    self.measured_energy += (sample as f64).powi(2);
                }
            }
            if measure {
                self.measured_frames += 480;
            }
            self.pending.drain(..480 * 2);
        }
    }

    fn exercise_switch_and_starvation(
        output_drift_ppm: f64,
        worker_period_ms: usize,
        legacy: bool,
    ) -> u64 {
        let mut rig = Rig::new();
        let output_period_ms = 10.0 / (1.0 + output_drift_ppm * 1e-6);
        let mut next_output_ms = 0.0;
        let mut total_injected_underruns = 0;
        let mut settled_underruns = 0;
        // Switch down, then back up, with an intentional capture-worker scheduling stall each
        // time. Output keeps running. Continuous input resumes without a track gap or restart.
        for ms in 0..15_000usize {
            if ms == 4000 || ms == 8000 {
                total_injected_underruns += rig.fifo.stats().underruns;
                rig.reconfigure(if ms == 4000 { 256 } else { 512 });
            }
            if ms % 10 == 0 {
                rig.capture_packet();
            }
            let stalled = (2000..2180).contains(&ms)
                || (4000..4180).contains(&ms)
                || (8000..8180).contains(&ms);
            if !stalled && ms % worker_period_ms == 0 {
                rig.render_available(legacy);
            }
            if ms as f64 >= next_output_ms {
                rig.output_event(ms >= 10_000);
                next_output_ms += output_period_ms;
            }
            // By one second after the last forced stall, fresh continuous audio must already
            // have rebuilt the cushion. Do not let a later silent track hide a stuck pipeline.
            if ms == 9200 {
                if !legacy {
                    assert!(!rig.fifo.is_repriming());
                }
                settled_underruns = rig.fifo.stats().underruns;
            }
        }
        total_injected_underruns += rig.fifo.stats().underruns;
        assert!(
            total_injected_underruns >= 3,
            "faults did not exercise underrun/re-prime"
        );
        let post_recovery_underruns = rig.fifo.stats().underruns - settled_underruns;
        assert!(
            rig.device.lost_packets > 0,
            "fault did not overflow the capture ring"
        );
        if legacy {
            return post_recovery_underruns;
        }
        assert_eq!(
            rig.fifo.stats().underruns,
            settled_underruns,
            "underruns continued after recovery at {output_drift_ppm} ppm"
        );
        assert!(rig.fifo.stats().reprimes > 0);
        assert!(rig.fifo.stats().reprime_silent_blocks > 0);
        assert!(rig.measured_frames > 4 * 48_000);
        assert!(
            rig.measured_energy / (rig.measured_frames * 2) as f64 > 0.003,
            "recovery silently muted the continuing tone"
        );
        assert!(
            rig.max_pending <= 480 + 2 * 512,
            "unbounded endpoint backlog"
        );
        post_recovery_underruns
    }

    #[test]
    fn continuous_source_recovers_after_stall_and_live_block_switches() {
        exercise_switch_and_starvation(0.0, 1, false);
    }

    #[test]
    fn independent_fast_and_slow_device_clocks_recover_without_a_track_gap() {
        exercise_switch_and_starvation(200.0, 1, false);
        exercise_switch_and_starvation(-200.0, 1, false);
    }

    #[test]
    fn coalesced_capture_events_recover_where_one_packet_policy_keeps_underrunning() {
        // Device packets arrive every 10 ms, while a worker service opportunity every 14 ms
        // models recurring notification coalescing. All-packet service must still sustain 48 kHz.
        let legacy_drops = exercise_switch_and_starvation(200.0, 14, true);
        assert!(
            legacy_drops > 10,
            "negative control failed to reproduce the sustained storm"
        );
        assert_eq!(exercise_switch_and_starvation(200.0, 14, false), 0);
    }

    #[test]
    fn idle_and_reprime_do_not_wind_the_drift_controller() {
        let mut stream = OutputResampler::new(1.0);
        let mut pending = Vec::new();
        // Start with a real nonzero correction so a masked update cannot pass by returning zero.
        for _ in 0..32 {
            pending.clear();
            stream.process_into(Pull::Block, &mut vec![0.0; 1024], 2, 4, false, &mut pending).unwrap();
        }
        let before = stream.correction_ppm();
        assert!(before.abs() > 0.0);
        for (pull, idle) in [
            (Pull::Underrun, false), (Pull::Repriming, false),
            (Pull::Underrun, true), (Pull::Repriming, true), (Pull::Block, true),
        ] {
            for _ in 0..100 {
                pending.clear();
                stream
                    .process_into(pull, &mut vec![0.0; 1024], 0, 4, idle, &mut pending)
                    .unwrap();
                assert_eq!(stream.correction_ppm(), before);
            }
        }
    }
}
