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
    struct FaultyDevice {
        inner: Device,
        failure: &'static str,
        reads: usize,
    }
    impl PacketSource for FaultyDevice {
        fn pending_frames(&self) -> Result<usize, String> {
            if self.failure == "pending" {
                return Err("device invalidated on query".into());
            }
            self.inner.pending_frames()
        }
        fn wait(&mut self, timeout: u32) -> Result<bool, String> {
            if self.failure == "wait" {
                return Err("wait failed".into());
            }
            if self.failure == "arrival during wait" {
                self.inner.push(240, 0.25, false, false);
            }
            self.inner.wait(timeout)
        }
        fn read_packet(&mut self, out: &mut Vec<f32>) -> Result<CaptureFlags, String> {
            if self.failure == "read" || (self.failure == "second read" && self.reads == 1) {
                return Err("device invalidated on read".into());
            }
            self.reads += 1;
            self.inner.read_packet(out)
        }
    }

    #[test]
    fn arrivals_during_event_wait_are_serviced_in_the_same_call() {
        let mut d = FaultyDevice {
            inner: Device::new(),
            failure: "arrival during wait",
            reads: 0,
        };
        let mut pcm = vec![0.75]; // callers may append; preserve their existing samples
        assert!(read_available(&mut d, &mut pcm, 50).unwrap().is_some());
        assert_eq!(d.inner.waits, 1);
        assert_eq!(d.reads, 1);
        assert_eq!(pcm[0], 0.75);
        assert_eq!(&pcm[1..], vec![0.25; 480]);
    }

    #[test]
    fn device_errors_propagate_instead_of_masquerading_as_idle_or_success() {
        for failure in ["pending", "wait", "read", "second read"] {
            let mut d = FaultyDevice {
                inner: Device::new(),
                failure,
                reads: 0,
            };
            if failure != "wait" {
                d.inner.push(240, 0.25, false, false);
                d.inner.push(240, -0.25, false, false);
            }
            let mut pcm = vec![0.75];
            assert!(read_available(&mut d, &mut pcm, 50).is_err(), "{failure}");
            assert_eq!(pcm[0], 0.75);
            // A mid-batch error may leave partial samples in the caller's scratch. It still must
            // return Err so the engine discards that read rather than rendering it as a full batch.
            assert_eq!(pcm.len(), if failure == "second read" { 481 } else { 1 });
        }
    }

}
