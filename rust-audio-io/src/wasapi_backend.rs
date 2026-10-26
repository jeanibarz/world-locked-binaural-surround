//! WASAPI backend (Windows-only): loopback capture + event-driven shared-mode render + MMCSS
//! "Pro Audio" elevation. Productises the R-S2 probe behind the crate's [`AudioSource`] /
//! [`AudioOutput`] traits, so the engine is device-agnostic and a Track-B SYSVAD capture drops in.

use std::collections::VecDeque;

use wasapi::{
    initialize_mta, AudioCaptureClient, AudioClient, AudioRenderClient, Device, DeviceEnumerator,
    Direction, Handle, SampleType, StreamMode, WaveFormat,
};

use crate::traits::{AudioOutput, AudioSource, CaptureFlags, StreamFormat};

#[link(name = "avrt")]
extern "system" {
    fn AvSetMmThreadCharacteristicsW(task: *const u16, taskindex: *mut u32) -> isize;
}

/// Elevate the calling thread to the MMCSS "Pro Audio" class (glitch-resistant scheduling), exactly
/// as the engine's render thread relies on. Returns whether it succeeded. Proven to work from Rust
/// in R-S2. Call from the thread that does the real-time I/O.
pub fn set_pro_audio() -> bool {
    let name: Vec<u16> = "Pro Audio".encode_utf16().chain(std::iter::once(0)).collect();
    let mut idx: u32 = 0;
    let h = unsafe { AvSetMmThreadCharacteristicsW(name.as_ptr(), &mut idx) };
    h != 0
}

/// Initialise COM (MTA) for this thread — call once before building devices.
///
/// Tolerant of a thread whose COM apartment another library already initialised: `CoInitializeEx`
/// returns `S_OK`/`S_FALSE` (already initialised, fine), `RPC_E_CHANGED_MODE` (already initialised in
/// a different apartment model — still usable for our calls), or `RPC_E_TOO_LATE`. Treating those as
/// success stops a hard abort when e.g. nokhwa/MediaFoundation touched COM on this thread first
/// (BUG-35: the `?` here used to abort the whole engine with `0x80010106`).
pub fn init_com() -> Result<(), String> {
    let hr = initialize_mta();
    if hr.is_ok() {
        return Ok(()); // S_OK or S_FALSE
    }
    const RPC_E_CHANGED_MODE: i32 = 0x8001_0106u32 as i32;
    const RPC_E_TOO_LATE: i32 = 0x8001_0119u32 as i32;
    match hr.0 {
        RPC_E_CHANGED_MODE | RPC_E_TOO_LATE => Ok(()),
        _ => Err(format!("initialize_mta: {hr:?}")),
    }
}

/// Resolve a render endpoint by name. An exact (case-insensitive) friendly-name match wins; failing
/// that a unique case-insensitive substring match is used; if several endpoints contain the string
/// the match is **ambiguous** and we error listing them, rather than silently grabbing the first
/// (which could be the wrong, e.g. 2-channel, endpoint — BUG-18).
fn find_render_device(want: &str) -> Result<Device, String> {
    let enumerator = DeviceEnumerator::new().map_err(|e| e.to_string())?;
    let coll = enumerator.get_device_collection(&Direction::Render).map_err(|e| e.to_string())?;
    let n = coll.get_nbr_devices().map_err(|e| e.to_string())?;
    let wl = want.to_lowercase();
    let mut substr: Vec<(String, Device)> = Vec::new();
    for i in 0..n {
        let d = coll.get_device_at_index(i).map_err(|e| e.to_string())?;
        let name = d.get_friendlyname().unwrap_or_default();
        let nl = name.to_lowercase();
        if nl == wl {
            return Ok(d); // exact match wins outright
        }
        if nl.contains(&wl) {
            substr.push((name, d));
        }
    }
    match substr.len() {
        0 => Err(format!("no render device matching {want:?}")),
        1 => Ok(substr.pop().unwrap().1),
        _ => {
            let names: Vec<String> = substr.into_iter().map(|(n, _)| n).collect();
            Err(format!("{want:?} is ambiguous — matches {names:?}; use a more specific name"))
        }
    }
}

fn sample_kind(fmt: &WaveFormat) -> Result<(SampleType, u16), String> {
    let bits = fmt.get_bitspersample();
    let st = fmt.get_subformat().map_err(|e| e.to_string())?;
    Ok((st, bits))
}

/// Reject a PCM format the byte<->f32 converters don't handle, at open time, so an unsupported depth
/// can't silently produce digital silence (capture) or a short/garbage buffer (render) — BUG-20.
fn check_format(st: SampleType, bits: u16) -> Result<(), String> {
    match (st, bits) {
        (SampleType::Float, 32)
        | (SampleType::Int, 16)
        | (SampleType::Int, 24)
        | (SampleType::Int, 32) => Ok(()),
        _ => Err(format!("unsupported PCM format: {st:?} {bits}-bit (need Float32 or Int16/24/32)")),
    }
}

/// Convert one interleaved sample's raw little-endian bytes to f32 in [-1,1]. 24-bit is 3 packed
/// bytes, sign-extended from bit 23.
#[inline]
fn bytes_to_f32(b: &[u8], st: SampleType, bits: u16) -> f32 {
    match (st, bits) {
        (SampleType::Float, 32) => f32::from_le_bytes([b[0], b[1], b[2], b[3]]),
        (SampleType::Int, 16) => i16::from_le_bytes([b[0], b[1]]) as f32 / 32768.0,
        (SampleType::Int, 24) => {
            // pack 3 LE bytes into the high 24 bits of an i32, then arithmetic-shift down to sign-extend
            let v = ((b[0] as i32) << 8) | ((b[1] as i32) << 16) | ((b[2] as i32) << 24);
            (v >> 8) as f32 / 8_388_608.0
        }
        (SampleType::Int, 32) => i32::from_le_bytes([b[0], b[1], b[2], b[3]]) as f32 / 2147483648.0,
        _ => 0.0,
    }
}

/// Loopback capture of a render endpoint (what the owner hears), as an [`AudioSource`].
pub struct WasapiLoopback {
    _client: AudioClient,
    capture: AudioCaptureClient,
    event: Handle,
    format: StreamFormat,
    bytes_per_sample: usize,
    sample_type: SampleType,
    bits: u16,
    scratch: VecDeque<u8>,
}

impl WasapiLoopback {
    /// Open loopback capture on the first render endpoint whose friendly name contains `want`
    /// (default target the engine's active output — an idle endpoint yields no packets).
    pub fn open(want: &str) -> Result<Self, String> {
        let device = find_render_device(want)?;
        let mut client = device.get_iaudioclient().map_err(|e| e.to_string())?;
        let format = client.get_mixformat().map_err(|e| e.to_string())?;
        let (st, bits) = sample_kind(&format)?;
        check_format(st, bits)?;
        let sr = format.get_samplespersec();
        let ch = format.get_nchannels();
        let blockalign = format.get_blockalign() as usize;
        // Shared event-driven mode: pass 0 for the buffer duration so the audio engine picks the
        // default (minimum) buffer matching its period, the MS-recommended value. The device `min_time`
        // we used before was aggressively small and risked underruns on some endpoints (BUG-33).
        let mode = StreamMode::EventsShared { autoconvert: false, buffer_duration_hns: 0 };
        // Direction::Capture on a *render* endpoint == loopback capture.
        client.initialize_client(&format, &Direction::Capture, &mode).map_err(|e| e.to_string())?;
        let event = client.set_get_eventhandle().map_err(|e| e.to_string())?;
        let capture = client.get_audiocaptureclient().map_err(|e| e.to_string())?;
        client.start_stream().map_err(|e| e.to_string())?;
        Ok(Self {
            _client: client,
            capture,
            event,
            format: StreamFormat { sample_rate: sr, channels: ch },
            bytes_per_sample: blockalign / ch as usize,
            sample_type: st,
            bits,
            scratch: VecDeque::new(),
        })
    }
}

impl AudioSource for WasapiLoopback {
    fn format(&self) -> StreamFormat {
        self.format
    }

    fn read(&mut self, out: &mut Vec<f32>, timeout_ms: u32) -> Result<Option<CaptureFlags>, String> {
        if self.event.wait_for_event(timeout_ms).is_err() {
            return Ok(None); // timed out
        }
        let info = self.capture.read_from_device_to_deque(&mut self.scratch).map_err(|e| e.to_string())?;
        // Drain whole samples from the byte deque → f32.
        let bps = self.bytes_per_sample;
        let mut buf = [0u8; 4];
        while self.scratch.len() >= bps {
            for b in buf.iter_mut().take(bps) {
                *b = self.scratch.pop_front().unwrap();
            }
            out.push(bytes_to_f32(&buf[..bps], self.sample_type, self.bits));
        }
        Ok(Some(CaptureFlags { discontinuity: info.flags.data_discontinuity, silent: info.flags.silent }))
    }

    fn stop(&mut self) -> Result<(), String> {
        self._client.stop_stream().map_err(|e| e.to_string())
    }
}

/// Event-driven shared-mode render output, as an [`AudioOutput`].
pub struct WasapiRender {
    client: AudioClient,
    render: AudioRenderClient,
    event: Handle,
    format: StreamFormat,
    bytes_per_sample: usize,
    channels: usize,
    sample_type: SampleType,
    bits: u16,
}

impl WasapiRender {
    /// Open shared-mode event-driven render on the endpoint whose name contains `want`.
    pub fn open(want: &str) -> Result<Self, String> {
        let device = find_render_device(want)?;
        let mut client = device.get_iaudioclient().map_err(|e| e.to_string())?;
        let format = client.get_mixformat().map_err(|e| e.to_string())?;
        let (st, bits) = sample_kind(&format)?;
        check_format(st, bits)?;
        let sr = format.get_samplespersec();
        let ch = format.get_nchannels();
        let blockalign = format.get_blockalign() as usize;
        // Shared event-driven mode: pass 0 for the buffer duration so the audio engine picks the
        // default (minimum) buffer matching its period, the MS-recommended value. The device `min_time`
        // we used before was aggressively small and risked underruns on some endpoints (BUG-33).
        let mode = StreamMode::EventsShared { autoconvert: false, buffer_duration_hns: 0 };
        client.initialize_client(&format, &Direction::Render, &mode).map_err(|e| e.to_string())?;
        let event = client.set_get_eventhandle().map_err(|e| e.to_string())?;
        let render = client.get_audiorenderclient().map_err(|e| e.to_string())?;
        client.start_stream().map_err(|e| e.to_string())?;
        Ok(Self {
            client,
            render,
            event,
            format: StreamFormat { sample_rate: sr, channels: ch },
            bytes_per_sample: blockalign / ch as usize,
            channels: ch as usize,
            sample_type: st,
            bits,
        })
    }

    fn f32_to_bytes(&self, v: f32, out: &mut Vec<u8>) {
        match (self.sample_type, self.bits) {
            (SampleType::Float, 32) => out.extend_from_slice(&v.to_le_bytes()),
            (SampleType::Int, 16) => {
                let s = (v.clamp(-1.0, 1.0) * 32767.0) as i16;
                out.extend_from_slice(&s.to_le_bytes());
            }
            (SampleType::Int, 24) => {
                let s = (v.clamp(-1.0, 1.0) * 8_388_607.0) as i32;
                let b = s.to_le_bytes();
                out.extend_from_slice(&b[0..3]); // low 3 bytes = 24-bit LE
            }
            (SampleType::Int, 32) => {
                let s = (v.clamp(-1.0, 1.0) * 2147483647.0) as i32;
                out.extend_from_slice(&s.to_le_bytes());
            }
            _ => {}
        }
    }
}

impl AudioOutput for WasapiRender {
    fn format(&self) -> StreamFormat {
        self.format
    }

    fn frames_needed(&mut self) -> Result<usize, String> {
        self.client.get_available_space_in_frames().map(|n| n as usize).map_err(|e| e.to_string())
    }

    fn write(&mut self, interleaved: &[f32]) -> Result<(), String> {
        let frames = interleaved.len() / self.channels;
        if frames == 0 {
            return Ok(());
        }
        let mut bytes = Vec::with_capacity(frames * self.channels * self.bytes_per_sample);
        for &v in interleaved {
            self.f32_to_bytes(v, &mut bytes);
        }
        self.render.write_to_device(frames, &bytes, None).map_err(|e| e.to_string())
    }

    fn wait(&mut self, timeout_ms: u32) -> Result<bool, String> {
        Ok(self.event.wait_for_event(timeout_ms).is_ok())
    }

    fn stop(&mut self) -> Result<(), String> {
        self.client.stop_stream().map_err(|e| e.to_string())
    }
}

/// Just enumerate render endpoints (name list) — for the probe to report what it can target.
pub fn list_render_endpoints() -> Result<Vec<String>, String> {
    let enumerator = DeviceEnumerator::new().map_err(|e| e.to_string())?;
    let coll = enumerator.get_device_collection(&Direction::Render).map_err(|e| e.to_string())?;
    let n = coll.get_nbr_devices().map_err(|e| e.to_string())?;
    let mut out = Vec::new();
    for i in 0..n {
        let d = coll.get_device_at_index(i).map_err(|e| e.to_string())?;
        out.push(d.get_friendlyname().unwrap_or_default());
    }
    Ok(out)
}

/// Enumerate render endpoints with their shared-mode mix format (name, channels, sample-rate).
/// Channels/sr are `0` when an endpoint's format cannot be read. Used by `--list-devices` so the
/// operator can pick an 8-channel (7.1) `--source` and the right `--output` by name substring.
pub fn list_render_endpoints_detailed() -> Result<Vec<(String, u16, u32)>, String> {
    let enumerator = DeviceEnumerator::new().map_err(|e| e.to_string())?;
    let coll = enumerator.get_device_collection(&Direction::Render).map_err(|e| e.to_string())?;
    let n = coll.get_nbr_devices().map_err(|e| e.to_string())?;
    let mut out = Vec::new();
    for i in 0..n {
        let d = coll.get_device_at_index(i).map_err(|e| e.to_string())?;
        let name = d.get_friendlyname().unwrap_or_default();
        let (ch, sr) = match d.get_iaudioclient().and_then(|c| c.get_mixformat()) {
            Ok(fmt) => (fmt.get_nchannels(), fmt.get_samplespersec()),
            Err(_) => (0u16, 0u32),
        };
        out.push((name, ch, sr));
    }
    Ok(out)
}
