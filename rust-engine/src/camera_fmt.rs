//! Pure camera capture-mode selection — chosen *by us* from the device's real capability list,
//! not by nokhwa's `RequestedFormatType::Closest`.
//!
//! nokhwa 0.10's `Closest` matcher is too brittle for a product that must open whatever webcam the
//! owner has: it only considers modes whose `FrameFormat` *equals* the one requested, and its
//! frame-rate step even demands an *exact* resolution match. So a hard-coded `MJPEG` request fails
//! outright — `"Could not initialize MediaFoundation: Failed to fulfill requested format"` — on the
//! many Windows cameras (most integrated laptop webcams) that expose only YUYV/NV12 (issue #171
//! regression). We instead enumerate the device's compatible modes and pick the closest ourselves.
//!
//! Lives outside the `#![cfg(windows)]` `vision` module precisely so this selection logic is
//! compiled and unit-tested on Linux/CI, where no camera (and no MediaFoundation) exists.

/// Choose the capture mode closest to the requested `w`×`h`@`fps` from `modes`.
///
/// Each mode is `(width, height, fps, is_mjpeg)`. Modes are ranked by, in order: squared pixel
/// distance of the resolution, absolute frame-rate distance, then MJPEG-first as a tiebreak
/// (compressed → less USB bandwidth, so preferred only when resolution and fps are otherwise
/// equal). Returns the index of the best mode, or `None` when `modes` is empty — in which case the
/// caller leaves the already-streaming probe format in place rather than failing the open.
#[must_use]
pub fn pick_camera_mode(modes: &[(u32, u32, u32, bool)], w: u32, h: u32, fps: u32) -> Option<usize> {
    modes
        .iter()
        .enumerate()
        .min_by_key(|(_, &(mw, mh, mfps, mjpeg))| {
            let dx = i64::from(mw) - i64::from(w);
            let dy = i64::from(mh) - i64::from(h);
            let res_dist = dx * dx + dy * dy;
            let fps_dist = (i64::from(mfps) - i64::from(fps)).abs();
            (res_dist, fps_dist, u8::from(!mjpeg))
        })
        .map(|(i, _)| i)
}

#[cfg(test)]
mod tests {
    use super::pick_camera_mode;

    #[test]
    fn empty_modes_yields_none() {
        assert_eq!(pick_camera_mode(&[], 640, 480, 30), None);
    }

    #[test]
    fn exact_resolution_and_fps_wins() {
        let modes = [(1920, 1080, 30, true), (640, 480, 30, false), (320, 240, 30, true)];
        assert_eq!(pick_camera_mode(&modes, 640, 480, 30), Some(1));
    }

    #[test]
    fn picks_a_yuyv_only_camera_despite_mjpeg_request_intent() {
        // The device offers NO MJPEG at all (the #171 failure case). We still open at the best
        // resolution instead of failing.
        let modes = [(1280, 720, 30, false), (640, 480, 30, false), (160, 120, 30, false)];
        assert_eq!(pick_camera_mode(&modes, 640, 480, 30), Some(1));
    }

    #[test]
    fn nearest_resolution_when_exact_absent() {
        // No 640×480; 848×480 (dist 208²) is closer than 320×240 (dist 320²+240²).
        let modes = [(320, 240, 30, true), (848, 480, 30, true), (1280, 720, 30, true)];
        assert_eq!(pick_camera_mode(&modes, 640, 480, 30), Some(1));
    }

    #[test]
    fn closest_fps_at_the_chosen_resolution() {
        let modes = [(640, 480, 15, true), (640, 480, 60, true), (640, 480, 30, true)];
        assert_eq!(pick_camera_mode(&modes, 640, 480, 30), Some(2));
    }

    #[test]
    fn mjpeg_breaks_a_resolution_and_fps_tie() {
        let modes = [(640, 480, 30, false), (640, 480, 30, true)];
        assert_eq!(pick_camera_mode(&modes, 640, 480, 30), Some(1));
    }
}
