//! `audio-io` — the zero-Python audio I/O layer for the world-lock engine (RFC:
//! standalone-distributable-and-rust-engine, slice 3).
//!
//! Replaces `sounddevice` / PyAudioWPatch with a native WASAPI backend behind device-agnostic
//! traits ([`AudioSource`] / [`AudioOutput`]), plus the cushioned drift-FIFO ([`CushionFifo`]) and
//! MMCSS "Pro Audio" elevation — the audio-robustness pieces proven in R-S2 (loopback robust over
//! 1.44M frames/30s, 0 timeouts; MMCSS from Rust works; +276 ppm render-clock drift is
//! cushion-absorbable). The trait seam means a Track-B SYSVAD capture drops in later.
//!
//! The FIFO and traits are pure/cross-platform (unit-tested on WSL/CI); the WASAPI backend and the
//! `audio_probe` measurement binary are Windows-only (`cfg(windows)`), cross-compiled for the host.

pub mod fifo;
pub mod traits;

pub use fifo::{CushionFifo, FifoStats, Pull};
pub use traits::{AudioOutput, AudioSource, CaptureFlags, StreamFormat};

#[cfg(windows)]
pub mod wasapi_backend;

/// Cushion depth in blocks for a target latency, matching `voicemeeter_passthrough`:
/// `max(2, round(latency_ms·sr/block))`, and the backlog cap is `2·cushion`.
pub fn cushion_blocks(latency_ms: f64, sample_rate: u32, block: usize) -> usize {
    let c = (latency_ms * 1e-3 * sample_rate as f64 / block as f64).round() as i64;
    (c.max(2)) as usize
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cushion_math_matches_engine() {
        // 20 ms @ 48 kHz, block 256 -> round(20e-3*48000/256)=round(3.75)=4, floored at 2 -> 4.
        assert_eq!(cushion_blocks(20.0, 48_000, 256), 4);
        // tiny latency floors at 2 blocks
        assert_eq!(cushion_blocks(1.0, 48_000, 256), 2);
        // big block makes the cushion coarse: 20 ms @ 48k, block 2048 -> round(0.46875)=0 -> floor 2
        assert_eq!(cushion_blocks(20.0, 48_000, 2048), 2);
    }
}
