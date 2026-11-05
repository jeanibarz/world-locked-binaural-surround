//! 7.1 world-lock geometry: the speaker layout + the head-relative azimuths the render steers by.
//!
//! Mirrors `binaural_headtracker.surround.SPEAKER_LAYOUT_7P1` / `world.layout_7p1`: channels in
//! WASAPI 7.1 interleave order `(L, R, C, LFE, RL, RR, SL, SR)`, azimuth CCW from front (+ = left).
//! As the head turns by `yaw` (deg, + = left), a world-fixed speaker at `az` appears head-relative
//! at `az - yaw`; the renderer routes that channel to the nearest ring BRIR for that angle, which is
//! what keeps the 7.1 field locked to the room.

/// 7.1 channel labels in WASAPI interleave order.
pub const CHANNELS_7P1: [&str; 8] = ["L", "R", "C", "LFE", "RL", "RR", "SL", "SR"];

/// Nominal speaker azimuth (deg, CCW from front) per channel — the Dolby 7.1 reference angles.
pub fn speaker_azimuth(channel: &str) -> f64 {
    match channel {
        "L" => 30.0,
        "R" => -30.0,
        "C" => 0.0,
        "LFE" => 0.0,
        "SL" => 110.0,
        "SR" => -110.0,
        "RL" => 150.0,
        "RR" => -150.0,
        _ => 0.0,
    }
}

/// Index of the LFE channel in [`CHANNELS_7P1`].
pub const LFE_INDEX: usize = 3;

/// The directional ring channels (everything but LFE) and their input-column indices.
pub fn ring_indices() -> Vec<usize> {
    (0..CHANNELS_7P1.len()).filter(|&i| i != LFE_INDEX).collect()
}

/// Wrap an angle (deg) to (-180, 180].
pub fn wrap_deg(a: f64) -> f64 {
    (a + 180.0).rem_euclid(360.0) - 180.0
}

/// Per-channel head-relative azimuths (length 8) for a head yaw. `yaw_sign` flips handedness
/// (calibration). The LFE entry is present but ignored by the renderer.
pub fn head_relative_azimuths(yaw_deg: f64, yaw_sign: f64) -> [f64; 8] {
    let mut out = [0.0f64; 8];
    for (i, ch) in CHANNELS_7P1.iter().enumerate() {
        out[i] = wrap_deg(speaker_azimuth(ch) - yaw_sign * yaw_deg);
    }
    out
}

/// 5.1 channel labels in WASAPI interleave order `(L, R, C, LFE, SL, SR)` — the common Windows 5.1
/// layout (side surrounds at ±110°). Used when the source is 6-channel.
pub const CHANNELS_5P1: [&str; 6] = ["L", "R", "C", "LFE", "SL", "SR"];

/// Per-channel head-relative azimuths (length 6) for a 5.1 source. Same math as the 7.1 version but
/// over the 5.1 channel order, so input column 4/5 are the side surrounds, not the 7.1 rears.
pub fn head_relative_azimuths_5p1(yaw_deg: f64, yaw_sign: f64) -> [f64; 6] {
    let mut out = [0.0f64; 6];
    for (i, ch) in CHANNELS_5P1.iter().enumerate() {
        out[i] = wrap_deg(speaker_azimuth(ch) - yaw_sign * yaw_deg);
    }
    out
}

/// Published head yaw (deg, **positive = head turned left** — the convention the audio azimuths and
/// the radar both expect) from the pose solver's raw yaw, the captured neutral (in the solver's raw
/// convention), and the tracking direction.
///
/// `invert = false` is the default: the published yaw is just the raw offset from neutral, with the
/// solver's own handedness. The head-tracking direction is corrected upstream by un-mirroring the
/// camera frame (BUG #191: the operator's webcam delivers a horizontally-flipped image, which was
/// what reversed the yaw); correcting it there keeps the pose, the landmarks and the ear handedness
/// all consistent. `invert = true` is an advanced override that negates the offset, for a mount
/// whose handedness still runs the other way after the camera flip. The sign is applied once, here,
/// so the radar (which rotates by `-yaw`) and the audio (`speaker_az - yaw`) can never disagree in
/// direction.
pub fn published_yaw(raw_yaw: f64, neutral_raw: f64, invert: bool) -> f64 {
    let sign = if invert { -1.0 } else { 1.0 };
    // Wrap to (-180, 180] so a full-circle raw yaw minus a neutral never publishes an out-of-range
    // angle (e.g. raw 170 − neutral −20 = 190 → −170). This is a NO-OP for the current ±45°
    // operating range (|offset| ≤ 180 wraps to itself), and is the representation prerequisite for
    // the full-range estimator (RFC slice C0): once the pose source is a full-circle azimuth, the
    // published yaw stays a single-valued angle instead of folding or running past ±180°.
    wrap_deg(sign * (raw_yaw - neutral_raw))
}

/// New raw neutral when re-centring so the current heading reads 0°, preserving the direction sign.
/// Inverse of [`published_yaw`] in the neutral: from `yaw = sign * (raw_cur - neutral)` we get
/// `raw_cur = neutral + yaw / sign`, and for `sign ∈ {±1}`, `1/sign == sign`.
pub fn recenter_neutral(neutral_raw: f64, cur_yaw: f64, invert: bool) -> f64 {
    let sign = if invert { -1.0 } else { 1.0 };
    // Wrap so the new neutral stays a single-valued raw azimuth in (-180, 180] even when re-centring
    // past the ±180° seam (full-circle source). The re-centre invariant still holds — because
    // `published_yaw` also wraps, a 360° offset in the stored neutral cancels: see
    // `recenter_zeroes_across_the_wrap`. NO-OP in the current ±45° range.
    wrap_deg(neutral_raw + cur_yaw * sign)
}

/// A captured FaceMesh overlay for the cockpit camera preview (#185). Coordinates are per-mille
/// (0..1000) of the **un-mirrored** frame — the same frame fed to the pose pipeline and the
/// `/camera` preview, so the overlay aligns with the face — packed compactly for the 20 Hz
/// telemetry frame.
#[derive(Clone, Debug, PartialEq)]
pub struct LandmarkFrame {
    /// Interleaved `[x0,y0,x1,y1,…]`, each per-mille of frame width/height.
    pub pts: Vec<u16>,
    /// Bounding box `[x, y, w, h]` in per-mille, derived from the landmark extent (a small margin
    /// added), so the preview can draw a face box without plumbing the detector ROI through.
    pub box_xywh: [u16; 4],
}

/// Pack the pipeline's normalized FaceMesh landmarks (`x,y` ∈ [0,1] of width/height; the `z` is
/// unused by the overlay) into a compact per-mille [`LandmarkFrame`] with a derived, slightly-padded
/// bounding box. `None` for an empty landmark set. Pure (lives here, not in the `#![cfg(windows)]`
/// vision module) so native `cargo test` covers it.
pub fn pack_landmarks(landmarks: &[[f64; 3]]) -> Option<LandmarkFrame> {
    if landmarks.is_empty() {
        return None;
    }
    let q = |v: f64| -> u16 { (v.clamp(0.0, 1.0) * 1000.0).round() as u16 };
    let mut pts = Vec::with_capacity(landmarks.len() * 2);
    let (mut min_x, mut min_y, mut max_x, mut max_y) = (1000u16, 1000u16, 0u16, 0u16);
    for lm in landmarks {
        let (x, y) = (q(lm[0]), q(lm[1]));
        pts.push(x);
        pts.push(y);
        min_x = min_x.min(x);
        min_y = min_y.min(y);
        max_x = max_x.max(x);
        max_y = max_y.max(y);
    }
    // Pad the raw landmark extent by ~3% of the frame so the box frames the face rather than
    // clipping its outline, clamped to the frame.
    const PAD: u16 = 30;
    let bx = min_x.saturating_sub(PAD);
    let by = min_y.saturating_sub(PAD);
    let bw = (max_x + PAD).min(1000).saturating_sub(bx);
    let bh = (max_y + PAD).min(1000).saturating_sub(by);
    Some(LandmarkFrame { pts, box_xywh: [bx, by, bw, bh] })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pack_landmarks_quantizes_and_boxes() {
        // Empty set → nothing to draw.
        assert!(pack_landmarks(&[]).is_none());
        // Two points spanning a face region: per-mille rounding + a padded, clamped box.
        let lm = [[0.25, 0.40, 0.0], [0.75, 0.80, 0.0]];
        let f = pack_landmarks(&lm).unwrap();
        assert_eq!(f.pts, vec![250, 400, 750, 800]);
        // box = extent [250..750]×[400..800] padded by 30, clamped to [0,1000].
        assert_eq!(f.box_xywh, [220, 370, 560, 460]);
        // A point at the frame edge stays in range and the box never exceeds the frame.
        let edge = pack_landmarks(&[[0.0, 0.0, 0.0], [1.2, 1.0, 0.0]]).unwrap();
        assert_eq!(edge.pts, vec![0, 0, 1000, 1000]); // 1.2 clamps to 1000
        let [bx, by, bw, bh] = edge.box_xywh;
        assert!(bx as u32 + bw as u32 <= 1000 && by as u32 + bh as u32 <= 1000);
    }

    #[test]
    fn neutral_pose_is_the_speaker_layout() {
        let az = head_relative_azimuths(0.0, 1.0);
        assert_eq!(az[0], 30.0); // L
        assert_eq!(az[1], -30.0); // R
        assert_eq!(az[6], 110.0); // SL
    }

    #[test]
    fn turning_left_moves_speakers_right_relative_to_the_head() {
        // yaw +30 (head turns left): the front-left speaker is now straight ahead (30-30=0).
        let az = head_relative_azimuths(30.0, 1.0);
        assert!((az[0] - 0.0).abs() < 1e-9);
        // and the centre speaker is now to the head's right (-30).
        assert!((az[2] - (-30.0)).abs() < 1e-9);
    }

    #[test]
    fn five_one_layout_uses_side_surrounds() {
        let az = head_relative_azimuths_5p1(0.0, 1.0);
        assert_eq!(az[0], 30.0); // L
        assert_eq!(az[1], -30.0); // R
        assert_eq!(az[2], 0.0); // C
        assert_eq!(az[4], 110.0); // SL (side, not the 7.1 rear 150)
        assert_eq!(az[5], -110.0); // SR
    }

    #[test]
    fn ring_excludes_lfe() {
        let r = ring_indices();
        assert_eq!(r, vec![0, 1, 2, 4, 5, 6, 7]);
        assert!(!r.contains(&LFE_INDEX));
    }

    // --- #191: tracking direction corrected at the camera; sign here is a pass-through default ---

    // The default publishes the raw offset from neutral unchanged (the camera un-mirror upstream is
    // what makes the solver's raw yaw match the +left convention — BUG #191). With an un-mirrored
    // feed, a head-turn-left arrives as a larger raw yaw and publishes as a positive (left) yaw.
    #[test]
    fn default_direction_passes_through_solver_raw() {
        // neutral captured at raw 100; +10 raw past neutral publishes as +10, -10 as -10.
        assert_eq!(published_yaw(110.0, 100.0, false), 10.0);
        assert_eq!(published_yaw(90.0, 100.0, false), -10.0);
    }

    // The advanced invert override negates the offset, for a mount still reversed after the flip.
    #[test]
    fn invert_negates_the_offset() {
        assert_eq!(published_yaw(110.0, 100.0, true), -10.0);
        assert_eq!(published_yaw(90.0, 100.0, true), 10.0);
    }

    // Flipping the direction mirrors the published yaw around neutral — identical to recomputing
    // under the opposite sign (validates PoseState's in-place `yaw = -yaw` on toggle).
    #[test]
    fn invert_mirrors_around_neutral() {
        for raw in [60.0, 100.0, 137.0] {
            let a = published_yaw(raw, 100.0, false);
            let b = published_yaw(raw, 100.0, true);
            assert_eq!(a, -b);
        }
    }

    // Re-centring makes the current heading read 0, and a later move reads relative to the new
    // neutral under either direction.
    #[test]
    fn recenter_zeroes_and_holds_under_either_sign() {
        for invert in [false, true] {
            let neutral0 = 100.0;
            let raw_at_recenter = 140.0;
            let y = published_yaw(raw_at_recenter, neutral0, invert);
            let neutral1 = recenter_neutral(neutral0, y, invert);
            assert_eq!(published_yaw(raw_at_recenter, neutral1, invert), 0.0);
            // +10 raw past the new neutral
            let moved = published_yaw(raw_at_recenter + 10.0, neutral1, invert);
            assert_eq!(moved, if invert { -10.0 } else { 10.0 });
        }
    }

    // The radar (rotates the cone by -yaw) and the audio (front-centre azimuth = C - yaw_sign*yaw,
    // yaw_sign = +1) depend on the SAME published yaw with the SAME sign, so they always agree in
    // direction — the core of the #189 fix. Head-left (published +Y): the centre speaker swings to
    // the head's right (negative azimuth) and the radar cone rotates by -Y to match.
    // --- RFC slice C0: full-circle, wrap-safe published yaw (no-op in range) ---

    // Inside the current ±45° operating range, wrapping changes nothing — the published yaw is
    // bit-identical to the old `sign*(raw-neutral)`. This is the "C0 ships as a no-op" guarantee.
    #[test]
    fn published_yaw_is_unchanged_in_operating_range() {
        for (raw, neutral) in [(110.0, 100.0), (60.0, 100.0), (137.0, 100.0), (0.0, 0.0), (45.0, 0.0)]
        {
            for invert in [false, true] {
                let sign = if invert { -1.0 } else { 1.0 };
                // |offset| ≤ 180 here, so wrap is the identity and matches the pre-C0 formula.
                assert_eq!(published_yaw(raw, neutral, invert), sign * (raw - neutral));
            }
        }
    }

    // Past the seam, a full-circle raw azimuth minus a neutral wraps to a single-valued angle
    // instead of running past ±180°: the behaviour the full-range estimator needs. raw +170,
    // neutral −20 → offset 190 → published −170 (the head is 10° past straight-back, not 190°).
    #[test]
    fn published_yaw_wraps_full_circle() {
        assert!((published_yaw(170.0, -20.0, false) - (-170.0)).abs() < 1e-9);
        assert!((published_yaw(-170.0, 20.0, false) - 170.0).abs() < 1e-9);
        // and the audio azimuth is identical whether the published yaw is wrapped or not, because
        // head_relative_azimuths wraps again — this is why C0 is safe to land before the estimator.
        let wrapped = published_yaw(170.0, -20.0, false); // -170
        let unwrapped = 190.0;
        for ch in CHANNELS_7P1 {
            let a = wrap_deg(speaker_azimuth(ch) - wrapped);
            let b = wrap_deg(speaker_azimuth(ch) - unwrapped);
            assert!((a - b).abs() < 1e-9, "azimuth for {ch} differs across wrap");
        }
    }

    // The re-centre invariant survives the wrap: re-centring at any heading (including past ±90°)
    // makes the current heading read 0, and because published_yaw also wraps, the stored-neutral
    // 360° ambiguity cancels.
    #[test]
    fn recenter_zeroes_across_the_wrap() {
        for invert in [false, true] {
            for (neutral0, raw_now) in [(0.0, 140.0), (-20.0, 170.0), (100.0, -160.0), (175.0, -175.0)]
            {
                let y = published_yaw(raw_now, neutral0, invert);
                let neutral1 = recenter_neutral(neutral0, y, invert);
                assert!(neutral1 > -180.0 && neutral1 <= 180.0, "neutral stays in range");
                assert!(
                    published_yaw(raw_now, neutral1, invert).abs() < 1e-9,
                    "re-centred heading must read 0 (neutral0={neutral0}, raw={raw_now}, invert={invert})"
                );
            }
        }
    }

    #[test]
    fn radar_and_audio_agree_in_direction() {
        let yaw = published_yaw(110.0, 100.0, false); // head left (un-mirrored feed) -> +10
        assert_eq!(yaw, 10.0);
        let radar_rotation = -yaw; // SVG transform on the cone
        let front_center = head_relative_azimuths(yaw, 1.0)[2]; // "C"
        assert_eq!(front_center, radar_rotation); // same magnitude AND sign
        assert!(front_center < 0.0); // centre is now to the right, as expected for a left turn
    }
}
