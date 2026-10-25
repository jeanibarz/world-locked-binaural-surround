//! Device-agnostic audio I/O traits, so the world-lock engine depends on *capture* and *output*
//! abstractly and a backend (WASAPI today; a SYSVAD/Track-B kernel capture later) is a drop-in.
//!
//! Replaces the Python `sounddevice` / PyAudioWPatch layer. The engine's contract is small: pull
//! interleaved f32 frames from an [`AudioSource`], push interleaved f32 frames to an [`AudioOutput`].

/// A stream's PCM format.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct StreamFormat {
    pub sample_rate: u32,
    pub channels: u16,
}

/// One capture read's status flags (from the device), mirroring WASAPI's buffer flags.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct CaptureFlags {
    /// The device signalled a data discontinuity (a glitch / dropped packets upstream).
    pub discontinuity: bool,
    /// The device signalled this buffer is silent.
    pub silent: bool,
}

/// A capture source: a loopback tap or a device input. Interleaved f32, `format().channels` wide.
pub trait AudioSource {
    fn format(&self) -> StreamFormat;
    /// Block until the next buffer is available (or `timeout_ms` elapses) and append its interleaved
    /// f32 frames to `out`. Returns the flags for this read, or an error. `Ok(None)` = timed out.
    fn read(&mut self, out: &mut Vec<f32>, timeout_ms: u32) -> Result<Option<CaptureFlags>, String>;
    /// Stop and release the stream.
    fn stop(&mut self) -> Result<(), String>;
}

/// An output sink: event-driven shared-mode render. Interleaved f32, `format().channels` wide.
pub trait AudioOutput {
    fn format(&self) -> StreamFormat;
    /// How many frames the device wants right now (its current padding headroom).
    fn frames_needed(&mut self) -> Result<usize, String>;
    /// Write up to `frames` of interleaved f32 (len must be `frames * channels`).
    fn write(&mut self, interleaved: &[f32]) -> Result<(), String>;
    /// Block until the device is ready for more (its event), or `timeout_ms` elapses.
    fn wait(&mut self, timeout_ms: u32) -> Result<bool, String>;
    fn stop(&mut self) -> Result<(), String>;
}
