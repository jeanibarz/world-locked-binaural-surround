//! Synthetic-room integration coverage: real grid convolution, full per-ear EQ, then gain.
//! Filters are deliberately small; these tests verify ordering and signal accounting, not the
//! owner's room response, WASAPI scheduling, or perceived loudness.
use crate::hp_eq::{BiquadSpec, EarChain, OutputEq};
use crate::output_gain::GainStage;
use render_core_lib::{GridRender, Lfe};

fn render_eq(block: usize, head: f64, amplitude: f64) -> Vec<f64> {
    let angles = vec![30.0, -30.0, 0.0, 180.0, 90.0, -90.0, 150.0, -150.0];
    let taps = 1024;
    let mut left = vec![0.0; 8 * taps];
    let mut right = left.clone();
    for bus in 0..8 {
        left[bus * taps] = 0.2 + bus as f64 * 0.03;
        right[bus * taps] = 0.4 - bus as f64 * 0.02;
        left[bus * taps + 511] = 0.08;
        right[bus * taps + 255] = -0.04;
    }
    let mut renderer = GridRender::new(
        &left,
        &right,
        8,
        taps,
        angles.clone(),
        (0..8).collect(),
        8,
        64,
        block,
        Lfe::None,
    )
    .unwrap();
    let spec = BiquadSpec {
        ftype: "PK".into(),
        fc: 1800.0,
        gain_db: 5.0,
        q: Some(0.8),
        bw_oct: None,
        slope_db: None,
    };
    let ear_l = EarChain {
        gain: 0.7,
        fir: Some(vec![0.8, 0.15, -0.05]),
        specs: vec![spec.clone()],
    };
    let ear_r = EarChain {
        gain: 0.5,
        fir: Some(vec![0.9, -0.1, 0.05]),
        specs: vec![spec],
    };
    let mut eq = OutputEq::build(&ear_l, &ear_r, 48000, block).unwrap();
    let desired: Vec<f64> = angles.iter().map(|a| a - head).collect();
    let mut all = Vec::new();
    for batch in 0..12 {
        let mut input = vec![0.0; block * 8];
        for frame in 0..block {
            let t = (batch * block + frame) as f64;
            for channel in 0..8 {
                input[frame * 8 + channel] = amplitude * (t * 0.037 + channel as f64 * 0.1).sin();
            }
        }
        let mut output = vec![0.0; block * 2];
        renderer.process_into(&input, &desired, &mut output);
        eq.process_interleaved(&mut output);
        all.extend(output);
    }
    all
}

#[test]
fn multichannel_room_eq_gain_reference_across_head_positions_and_blocks() {
    for block in [256, 512] {
        for head in [-60.0, 0.0, 45.0] {
            let bus = render_eq(block, head, 0.7);
            assert!(bus.iter().any(|x| x.abs() > 0.1));
            for gain_db in [0.0, 6.0, 18.0] {
                let mut gain = GainStage::new(48000);
                let mut scratch = Vec::with_capacity(block * 2);
                gain.set_gain_db(gain_db);
                gain.process_into(&[0.0; 960], &mut scratch);
                gain.take_metrics();
                let factor = 10.0_f64.powf(gain_db / 20.0);
                let expected_clips = bus.iter().filter(|&&v| (v * factor).abs() > 1.0).count();
                let expected_peak = bus.iter().map(|v| (v * factor).abs()).fold(0.0, f64::max);
                let expected_rms = (bus.iter().map(|v| (v * factor).powi(2)).sum::<f64>()
                    / bus.len() as f64)
                    .sqrt();
                for chunk in bus.chunks(block * 2) {
                    gain.process_into(chunk, &mut scratch);
                    for (&actual, &sample) in scratch.iter().zip(chunk) {
                        assert_eq!(actual, (sample * factor).clamp(-1.0, 1.0) as f32);
                    }
                }
                let meter = gain.take_metrics();
                assert_eq!(meter.samples, bus.len() as u64);
                assert_eq!(meter.clipped_samples, expected_clips as u64);
                assert!((meter.peak_dbfs.unwrap() - 20.0 * expected_peak.log10()).abs() < 1e-9);
                assert!((meter.rms_dbfs.unwrap() - 20.0 * expected_rms.log10()).abs() < 1e-9);
                if gain_db == 18.0 {
                    assert!(expected_clips > 0);
                }
            }
        }
    }
}

#[test]
fn negative_trim_preserves_room_sum_above_full_scale_until_final_clamp() {
    let quiet = render_eq(256, 25.0, 0.2);
    let loud = render_eq(256, 25.0, 3.2);
    assert!(loud.iter().any(|v| v.abs() > 1.0));
    let mut gain = GainStage::new(48000);
    let mut output = Vec::new();
    gain.set_gain_db(-24.0);
    gain.process_into(&[0.0; 960], &mut output);
    gain.take_metrics();
    gain.process_into(&loud, &mut output);
    let factor = 10.0_f64.powf(-24.0 / 20.0);
    for ((&actual, &large), &small) in output.iter().zip(&loud).zip(&quiet) {
        assert!(
            (large - 16.0 * small).abs() < 1e-9,
            "render and EQ retain unclamped sum"
        );
        assert_eq!(actual, (large * factor).clamp(-1.0, 1.0) as f32);
    }
    assert_eq!(gain.take_metrics().clipped_samples, 0);
}
