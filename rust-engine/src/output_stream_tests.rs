use super::*;
use audio_io::{
    capture::{read_available, PacketSource},
    cushion_blocks, CaptureFlags, CushionFifo,
};
use render_core_lib::{GridRender, Lfe};
use std::collections::VecDeque;

/// A finite device packet ring and auto-reset notification. Packet arrivals coalesce into a
/// single signal, and a stalled client loses the oldest packets when the ring fills.
#[derive(Default)]
struct CaptureDevice {
    packets: VecDeque<Vec<f32>>,
    signalled: bool,
    lost_packets: usize,
    discontinuity: bool,
    queued_frames: usize,
    lost_frames: usize,
}
impl CaptureDevice {
    fn push(&mut self, packet: Vec<f32>) {
        // Capacity is 60 ms of capture frames, independent of packet fragmentation.
        while self.queued_frames + packet.len() / 8 > 2880 {
            let dropped = self
                .packets
                .pop_front()
                .expect("packet exceeds device capacity");
            self.queued_frames -= dropped.len() / 8;
            self.lost_frames += dropped.len() / 8;
            self.lost_packets += 1;
            self.discontinuity = true;
        }
        self.queued_frames += packet.len() / 8;
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
        let packet = self.packets.pop_front().ok_or("empty simulated capture")?;
        self.queued_frames -= packet.len() / 8;
        out.extend(packet);
        Ok(CaptureFlags {
            silent: false,
            discontinuity: std::mem::take(&mut self.discontinuity),
        })
    }
}

/// Virtual devices have separate clocks, variable packet sizes, and endpoint event periods.
/// The render worker accumulates capture packets into the selected block size; device periods
/// deliberately do not equal DSP blocks.
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
    measured: Vec<f32>,
    target_cushion: Option<usize>,
    last_output_energy: f64,
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
            measured: Vec::new(),
            target_cushion: None,
            last_output_energy: 0.0,
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
        GridRender::new(
            &left,
            &right,
            8,
            1024,
            vec![30.0, -30.0, 0.0, 180.0, 90.0, -90.0, 150.0, -150.0],
            (0..8).collect(),
            8,
            64,
            block,
            Lfe::None,
        )
        .unwrap()
    }
    fn reconfigure(&mut self, block: usize) {
        if self.block != block {
            self.renderer = Self::renderer(block);
        }
        self.block = block;
        let cushion = self
            .target_cushion
            .unwrap_or_else(|| cushion_blocks(40.0, 48_000, block));
        self.fifo = CushionFifo::new(cushion, cushion * 2, vec![0.0; block * 2]);
        // The engine clears its partial input on reconfiguration, but keeps endpoint-rate
        // pending samples and lets the next FIFO payload trigger the resampler's rebuild.
        self.captured.clear();
    }
    fn capture_packet(&mut self) {
        self.capture_frames(480);
    }
    fn capture_frames(&mut self, frames: usize) {
        let mut packet = Vec::with_capacity(frames * 8);
        for _ in 0..frames {
            let sample =
                (self.source_frame as f32 * std::f32::consts::TAU * 440.0 / 48_000.0).sin() * 0.1;
            packet.push(sample);
            packet.push(
                (self.source_frame as f32 * std::f32::consts::TAU * 880.0 / 48_000.0).sin() * 0.1,
            );
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
            let input: Vec<f64> = self
                .captured
                .drain(..self.block * 8)
                .map(|x| x as f64)
                .collect();
            let mut output = vec![0.0; self.block * 2];
            self.renderer.process_into(
                &input,
                &[30.0, -30.0, 0.0, 180.0, 90.0, -90.0, 150.0, -150.0],
                &mut output,
            );
            self.fifo
                .push(output.into_iter().map(|x| x as f32).collect());
        }
    }
    fn output_event(&mut self, measure: bool) {
        self.output_frames(480, measure);
    }
    fn output_frames(&mut self, frames: usize, measure: bool) {
        while self.pending.len() < frames * 2 {
            let fill = self.fifo.len();
            let (pull, block) = self.fifo.pull();
            let before = self.pending.len();
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
            assert!(self.pending.len() > before, "resampler made no output progress");
        }
        self.max_pending = self.max_pending.max(self.pending.len() / 2);
        self.last_output_energy = self.pending[..frames * 2]
            .iter()
            .map(|&x| (x as f64).powi(2))
            .sum::<f64>()
            / (frames * 2) as f64;
        for &sample in &self.pending[..frames * 2] {
            assert!(
                sample.is_finite() && sample.abs() <= 0.2,
                "unsafe output sample {sample}"
            );
            if measure {
                self.measured_energy += (sample as f64).powi(2);
            }
        }
        if measure {
            self.measured_frames += frames;
            self.measured.extend_from_slice(&self.pending[..frames * 2]);
        }
        self.pending.drain(..frames * 2);
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
        let stalled =
            (2000..2180).contains(&ms) || (4000..4180).contains(&ms) || (8000..8180).contains(&ms);
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
        stream
            .process_into(Pull::Block, &mut vec![0.0; 1024], 2, 4, false, &mut pending)
            .unwrap();
    }
    let before = stream.correction_ppm();
    assert!(before.abs() > 0.0);
    for (pull, idle) in [
        (Pull::Underrun, false),
        (Pull::Repriming, false),
        (Pull::Underrun, true),
        (Pull::Repriming, true),
        (Pull::Block, true),
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

#[derive(Clone, Copy, Debug)]
struct Scenario {
    rate: usize,
    cushion: usize,
    capture_ppm: f64,
    output_ppm: f64,
    fragmented: bool,
    variable_events: bool,
    oversized_events: bool,
    stall_ms: usize,
}

/// Event times are derived from frames / real device rate, rather than rounding device
/// periods to a millisecond. A 96-frame request at 96 kHz consumes exactly 1 ms of audio;
/// a 960-frame request at 44.1 kHz consumes 21.77 ms. Worker opportunities alone are 1 ms.
fn exercise_timed_scenario(s: Scenario) -> u64 {
    let mut rig = Rig::new();
    rig.target_cushion = Some(s.cushion);
    rig.stream = OutputResampler::new(s.rate as f64 / 48_000.0);
    rig.reconfigure(256);
    let capture_rate = 48_000.0 * (1.0 + s.capture_ppm * 1e-6);
    let output_rate = s.rate as f64 * (1.0 + s.output_ppm * 1e-6);
    let capture_sizes: &[usize] = if s.fragmented { &[96, 384] } else { &[480] };
    let output_sizes: &[usize] = if s.oversized_events || s.rate == 96_000 {
        &[96, 240, 441, 480, 960]
    } else if s.variable_events {
        &[96, 240, 441, 480]
    } else {
        &[480]
    };
    let mut ci = 0;
    let mut oi = 0;
    let mut next_capture = capture_sizes[0] as f64 / capture_rate;
    let mut next_output = output_sizes[0] as f64 / output_rate;
    let mut next_worker = 0.001f64;
    let faults = [1.0, 2.5, 4.0, 5.5, 7.0];
    let mut fault = 0;
    let mut check_at = 0.75;
    let mut baseline = None;
    let mut recovered_at = None;
    let mut signal_at = None;
    let mut recoveries = Vec::new();
    let mut late_underruns = 0;
    let mut total_underruns = 0;
    let mut total_reprimes = 0;
    let mut endpoint_frames = 0;
    let mut last_output_time = 0.0;
    loop {
        let now = next_capture.min(next_output).min(next_worker);
        if now > 9.0 {
            break;
        }
        // Apply each control transaction before any service opportunity at/after its time.
        if fault < faults.len() && now >= faults[fault] {
            if fault > 0 && !s.oversized_events {
                assert_stereo_tones(&rig.measured, s.rate, s);
            }
            rig.measured.clear();
            total_underruns += rig.fifo.stats().underruns;
            total_reprimes += rig.fifo.stats().reprimes;
            match fault {
                0 => rig.reconfigure(512),
                1 => rig.reconfigure(1024),
                2 => rig.reconfigure(256),
                3 => {
                    // A cushion-only update also replaces the FIFO in the engine.
                    rig.target_cushion = Some(if s.cushion == 3 { 4 } else { 3 });
                    rig.reconfigure(256);
                }
                _ => rig.reconfigure(512),
            }
            check_at = faults[fault] + s.stall_ms as f64 / 1000.0 + 0.15;
            baseline = None;
            recovered_at = None;
            signal_at = None;
            fault += 1;
        }
        if next_capture == now {
            rig.capture_frames(capture_sizes[ci % capture_sizes.len()]);
            ci += 1;
            next_capture += capture_sizes[ci % capture_sizes.len()] as f64 / capture_rate;
        }
        if next_worker == now {
            let stalled = faults
                .iter()
                .any(|&start| now >= start && now < start + s.stall_ms as f64 / 1000.0);
            if !stalled {
                rig.render_available(false);
            }
            next_worker += 0.001;
        }
        if next_output == now {
            let frames = output_sizes[oi % output_sizes.len()];
            rig.output_frames(frames, now >= check_at);
            if fault > 0
                && signal_at.is_none()
                && now > faults[fault - 1] + s.stall_ms as f64 / 1000.0
                && rig.last_output_energy > 0.001
            {
                signal_at = Some(now);
            }
            endpoint_frames += frames;
            last_output_time = now;
            oi += 1;
            next_output += output_sizes[oi % output_sizes.len()] as f64 / output_rate;
        }
        if fault > 0
            && recovered_at.is_none()
            && now > faults[fault - 1] + s.stall_ms as f64 / 1000.0
            && !rig.fifo.is_repriming()
            && rig.fifo.len() > 0
        {
            recovered_at = Some(now);
        }
        if now >= check_at {
            let stats = rig.fifo.stats();
            if let Some(previous) = baseline {
                if stats.underruns != previous {
                    println!("late underrun: {s:?}, fault {fault}, t={now:.6}");
                    late_underruns += stats.underruns - previous;
                }
                if !s.oversized_events {
                    assert_eq!(
                        stats.underruns, previous,
                        "continuing underruns after deadline: {s:?}, fault {fault}, t={now}"
                    );
                }
                baseline = Some(stats.underruns);
            } else {
                assert!(
                    !rig.fifo.is_repriming(),
                    "failed to recover by {check_at}: {s:?}"
                );
                baseline = Some(stats.underruns);
                if fault > 0 {
                    if !s.oversized_events {
                        assert_eq!(
                            stats.underruns, 1,
                            "multiple early underruns in fault {fault}: {s:?}"
                        );
                    }
                    recoveries.push((
                        fault,
                        recovered_at.expect("no observed recovery") - faults[fault - 1],
                        signal_at.expect("no audible-level output by recovery deadline")
                            - faults[fault - 1],
                        stats.underruns,
                    ));
                }
            }
        }
    }
    total_underruns += rig.fifo.stats().underruns;
    total_reprimes += rig.fifo.stats().reprimes;
    assert_eq!(fault, faults.len());
    assert_eq!(recoveries.len(), faults.len());
    assert!(
        total_underruns >= 5 && total_reprimes >= 5,
        "injected starvation did not exercise recovery: {s:?}"
    );
    if !s.oversized_events {
        assert_eq!(
            total_underruns,
            faults.len() as u64,
            "expected exactly one underrun per injected stall: {s:?}"
        );
        assert_eq!(
            total_reprimes,
            faults.len() as u64,
            "expected exactly one re-prime per injected stall: {s:?}"
        );
    }
    assert!(rig.device.lost_frames > 0);
    assert!(
        (endpoint_frames as f64 - last_output_time * output_rate).abs() < 0.001,
        "device events did not consume exactly elapsed endpoint frames"
    );
    let max_event = *output_sizes.iter().max().unwrap();
    let max_resampled_block = (1024.0 * s.rate as f64 / 48_000.0 * 1.004).ceil() as usize;
    assert!(
        rig.max_pending <= max_event + max_resampled_block,
        "unbounded pending output: {s:?}"
    );
    assert_stereo_tones(&rig.measured, s.rate, s);
    println!("{s:?}: recoveries (fault, FIFO seconds, output signal seconds, underruns)={recoveries:?}; total underruns={total_underruns}, reprimes={total_reprimes}, lost frames={}, pending max={}", rig.device.lost_frames, rig.max_pending);
    late_underruns
}

fn assert_stereo_tones(samples: &[f32], rate: usize, scenario: Scenario) {
    let frames = samples.len() / 2;
    assert!(frames > rate / 2);
    // Independently identify both ears by positive-going crossings. A channel swap doubles or
    // halves these estimates. Cross-correlation and per-ear energy detect duplicated/muted ears.
    let mut energies = [0.0f64; 2];
    let mut crossings = [0usize; 2];
    let mut dot = 0.0;
    for i in 1..frames {
        let l = samples[i * 2] as f64;
        let r = samples[i * 2 + 1] as f64;
        energies[0] += l * l;
        energies[1] += r * r;
        dot += l * r;
        for ear in 0..2 {
            if samples[(i - 1) * 2 + ear] <= 0.0 && samples[i * 2 + ear] > 0.0 {
                crossings[ear] += 1;
            }
        }
    }
    for ear in 0..2 {
        assert!(
            energies[ear] / frames as f64 > 0.003,
            "silent/weak ear {ear}: {scenario:?}"
        );
        let hz = crossings[ear] as f64 * rate as f64 / frames as f64;
        let expected = if ear == 0 { 440.0 } else { 880.0 };
        assert!(
            (hz - expected).abs() < 8.0,
            "wrong tone in ear {ear}: {hz} Hz, {scenario:?}"
        );
    }
    let correlation = dot / (energies[0] * energies[1]).sqrt();
    assert!(
        correlation.abs() < 0.03,
        "ear leakage/duplication: r={correlation}, {scenario:?}"
    );
}

#[test]
fn endpoint_rates_small_cushions_independent_clocks_and_packet_jitter_recover() {
    // Pairwise coverage keeps debug runtime small while exercising both clock directions at
    // every endpoint rate and each cushion. Capture and output drift independently in opposite
    // directions (400 ppm relative), rather than perturbing only the output clock.
    for (i, rate) in [44_100, 48_000, 96_000].into_iter().enumerate() {
        for cushion in [3, 4] {
            let sign = if cushion == 3 { 1.0 } else { -1.0 };
            exercise_timed_scenario(Scenario {
                rate,
                cushion,
                capture_ppm: sign * 200.0,
                output_ppm: -sign * 200.0,
                fragmented: (i + cushion) % 2 == 0,
                variable_events: true,
                oversized_events: false,
                stall_ms: if cushion == 3 { 80 } else { 180 },
            });
        }
    }
}

#[test]
fn capture_ring_headroom_is_measured_in_frames_not_packet_count() {
    for fragments in [&[480][..], &[96, 384][..]] {
        let mut device = CaptureDevice::default();
        for _ in 0..6 {
            for &frames in fragments {
                device.push(vec![0.0; frames * 8]);
            }
        }
        assert_eq!(device.queued_frames, 2880);
        assert_eq!(device.lost_frames, 0);
        for &frames in fragments {
            device.push(vec![0.0; frames * 8]);
        }
        assert_eq!(device.queued_frames, 2880);
        assert_eq!(device.lost_frames, 480);
    }
}

#[test]
fn output_request_larger_than_small_cushion_exposes_jitter_boundary() {
    // 960 / 44100 = 21.77 ms between endpoint events exceeds the 16 ms safety
    // margin of three 256-frame blocks. Drain-all capture cannot make that cushion
    // absorb every demand burst; this is deliberately NOT a supported recovery case.
    let late = exercise_timed_scenario(Scenario {
        rate: 44_100,
        cushion: 3,
        capture_ppm: 200.0,
        output_ppm: -200.0,
        fragmented: false,
        variable_events: true,
        oversized_events: true,
        stall_ms: 80,
    });
    assert!(
        late > 0,
        "boundary probe no longer reproduces a late underrun; revisit its documented limit"
    );
}

#[test]
fn silent_source_packets_keep_clock_running_but_short_capture_gap_triggers_reprime() {
    let mut rig = Rig::new();
    for _ in 0..100 {
        rig.capture_packet();
        rig.render_available(false);
        rig.output_event(false);
    }
    let before = rig.fifo.stats().underruns;
    for _ in 0..100 {
        // Actual captured silence is audio with timing; it must still traverse the renderer,
        // FIFO and resampler. No-packet idle below provides no clocked input at all.
        rig.device.push(vec![0.0; 480 * 8]);
        rig.render_available(false);
        rig.output_event(false);
    }
    assert_eq!(rig.fifo.stats().underruns, before);
    assert!(rig.last_output_energy < 1e-12);
    assert!(!rig.fifo.is_repriming());
    for _ in 0..20 {
        rig.render_available(false);
        rig.output_event(false);
    }
    assert!(rig.fifo.is_repriming());
    assert_eq!(rig.fifo.stats().underruns, before + 1);
    let frozen = rig.stream.correction_ppm();
    // Remain below the production idle-silence feeder's 250 ms threshold.
    for _ in 0..2 {
        rig.output_event(false);
        assert_eq!(rig.stream.correction_ppm(), frozen);
    }
    for _ in 0..100 {
        rig.capture_packet();
        rig.render_available(false);
        rig.output_event(false);
    }
    assert!(!rig.fifo.is_repriming());
    assert_eq!(rig.fifo.stats().underruns, before + 1);
    assert!(rig.last_output_energy > 0.003);
}
