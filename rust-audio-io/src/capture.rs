//! Shared-mode capture service: notifications announce available work, not individual packets.
//! The backend keeps packet decoding here behind a seam so coalesced events can be tested without
//! replacing the production queue-draining policy with a simulated copy.

use crate::CaptureFlags;

pub trait PacketSource {
    fn pending_frames(&self) -> Result<usize, String>;
    fn wait(&mut self, timeout_ms: u32) -> Result<bool, String>;
    /// Append one packet, applying its own SILENT flag before returning the packet's flags.
    fn read_packet(&mut self, out: &mut Vec<f32>) -> Result<CaptureFlags, String>;
}

pub fn read_available(
    source: &mut impl PacketSource,
    out: &mut Vec<f32>,
    timeout_ms: u32,
) -> Result<Option<CaptureFlags>, String> {
    if source.pending_frames()? == 0 && !source.wait(timeout_ms)? {
        return Ok(None);
    }
    let start = out.len();
    let mut flags = CaptureFlags { discontinuity: false, silent: true };
    // Bound service time even if packets keep arriving. The next call checks the queue before
    // waiting, so hitting this fairness limit never strands work behind an already consumed event.
    for _ in 0..256 {
        if source.pending_frames()? == 0 { break; }
        let packet = source.read_packet(out)?;
        flags.discontinuity |= packet.discontinuity;
        flags.silent &= packet.silent;
    }
    Ok((out.len() > start).then_some(flags))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::VecDeque;

    struct Packet { frames: usize, value: f32, flags: CaptureFlags }
    struct Device { packets: VecDeque<Packet>, signalled: bool, waits: usize }
    impl Device {
        fn new() -> Self { Self { packets: VecDeque::new(), signalled: false, waits: 0 } }
        fn push(&mut self, frames: usize, value: f32, silent: bool, discontinuity: bool) {
            self.packets.push_back(Packet { frames, value, flags: CaptureFlags { silent, discontinuity } });
            // An auto-reset event stores one signal, regardless of the number of arrivals.
            self.signalled = true;
        }
    }
    impl PacketSource for Device {
        fn pending_frames(&self) -> Result<usize, String> {
            Ok(self.packets.front().map_or(0, |p| p.frames))
        }
        fn wait(&mut self, _: u32) -> Result<bool, String> {
            self.waits += 1;
            Ok(std::mem::take(&mut self.signalled))
        }
        fn read_packet(&mut self, out: &mut Vec<f32>) -> Result<CaptureFlags, String> {
            let p = self.packets.pop_front().ok_or("no packet")?;
            out.extend(std::iter::repeat(if p.flags.silent { 0.0 } else { p.value }).take(p.frames * 2));
            Ok(p.flags)
        }
    }

    #[test]
    fn coalesced_notification_drains_every_packet_with_its_own_flags() {
        let mut d = Device::new();
        d.push(128, 0.25, false, true);
        d.push(256, 99.0, true, false);
        d.push(96, -0.5, false, false);
        let mut pcm = Vec::new();
        let flags = read_available(&mut d, &mut pcm, 50).unwrap().unwrap();
        assert_eq!(pcm.len(), 480 * 2, "one wake must service all queued frames");
        assert_eq!(&pcm[..256], vec![0.25; 256]);
        assert_eq!(&pcm[256..768], vec![0.0; 512]);
        assert_eq!(&pcm[768..], vec![-0.5; 192]);
        assert!(flags.discontinuity);
        assert!(!flags.silent, "mixed batches are not entirely silent");
        assert!(d.packets.is_empty());
    }

    #[test]
    fn queued_packet_is_read_without_waiting_for_another_event() {
        let mut d = Device::new();
        d.push(480, 0.2, false, false);
        d.signalled = false; // prior wait already consumed the coalesced signal
        let mut pcm = Vec::new();
        assert!(read_available(&mut d, &mut pcm, 50).unwrap().is_some());
        assert_eq!(d.waits, 0);
        assert_eq!(pcm.len(), 960);
    }

    #[test]
    fn fairness_limit_leaves_queued_work_readable_without_an_event() {
        let mut d = Device::new();
        for _ in 0..300 { d.push(1, 0.1, false, false); }
        let mut pcm = Vec::new();
        read_available(&mut d, &mut pcm, 50).unwrap();
        assert_eq!(pcm.len(), 256 * 2);
        d.signalled = false;
        read_available(&mut d, &mut pcm, 50).unwrap();
        assert_eq!(pcm.len(), 300 * 2);
        assert_eq!(d.waits, 0);
    }

    #[test]
    fn entirely_silent_batch_is_still_delivered_as_clocked_frames() {
        let mut d = Device::new();
        d.push(240, 99.0, true, false);
        d.push(240, 99.0, true, true);
        let mut pcm = Vec::new();
        let flags = read_available(&mut d, &mut pcm, 50).unwrap().unwrap();
        assert!(flags.silent && flags.discontinuity);
        assert_eq!(pcm, vec![0.0; 960]);
    }

    #[test]
    fn spurious_notification_and_timeout_do_not_invent_audio() {
        let mut d = Device::new();
        let mut pcm = Vec::new();
        assert!(read_available(&mut d, &mut pcm, 50).unwrap().is_none());
        d.signalled = true;
        assert!(read_available(&mut d, &mut pcm, 50).unwrap().is_none());
        assert!(pcm.is_empty());
    }
}
