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
/// `invert = false` is the corrected default (BUG #189): the ONNX solver reports yaw with the
/// opposite handedness, so turning the head made the world anti-track; negating the raw offset
/// restores correct world-lock. `invert = true` keeps the solver's own handedness, for a
/// camera/mount that is mirrored. The sign is applied once, here, so the radar (which rotates by
/// `-yaw`) and the audio (`speaker_az - yaw`) can never disagree in direction.
pub fn published_yaw(raw_yaw: f64, neutral_raw: f64, invert: bool) -> f64 {
    let sign = if invert { 1.0 } else { -1.0 };
    sign * (raw_yaw - neutral_raw)
}

/// New raw neutral when re-centring so the current heading reads 0°, preserving the direction sign.
/// Inverse of [`published_yaw`] in the neutral: from `yaw = sign * (raw_cur - neutral)` we get
/// `raw_cur = neutral + yaw / sign`, and for `sign ∈ {±1}`, `1/sign == sign`.
pub fn recenter_neutral(neutral_raw: f64, cur_yaw: f64, invert: bool) -> f64 {
    let sign = if invert { 1.0 } else { -1.0 };
    neutral_raw + cur_yaw * sign
}

#[cfg(test)]
mod tests {
    use super::*;

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

    // --- #189: head-tracking direction at the pose source ---

    // The corrected default negates the solver's raw yaw. The ONNX solver reports a head-turn-left
    // as a NEGATIVE raw yaw (opposite handedness); negating yields the +left convention the rest of
    // the engine expects, so the world no longer anti-tracks.
    #[test]
    fn default_direction_negates_solver_raw() {
        // neutral captured at raw 100; a head-turn-left reported by the solver as raw 90 (-10 raw).
        assert_eq!(published_yaw(90.0, 100.0, false), 10.0); // published +10 = head left
        // and a head-turn-right (raw +10) publishes as -10.
        assert_eq!(published_yaw(110.0, 100.0, false), -10.0);
    }

    #[test]
    fn invert_keeps_solver_handedness() {
        assert_eq!(published_yaw(90.0, 100.0, true), -10.0);
        assert_eq!(published_yaw(110.0, 100.0, true), 10.0);
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
            assert_eq!(moved, if invert { 10.0 } else { -10.0 });
        }
    }

    // The radar (rotates the cone by -yaw) and the audio (front-centre azimuth = C - yaw_sign*yaw,
    // yaw_sign = +1) depend on the SAME published yaw with the SAME sign, so they always agree in
    // direction — the core of the #189 fix. Head-left (published +Y): the centre speaker swings to
    // the head's right (negative azimuth) and the radar cone rotates by -Y to match.
    #[test]
    fn radar_and_audio_agree_in_direction() {
        let yaw = published_yaw(90.0, 100.0, false); // head left -> +10
        assert_eq!(yaw, 10.0);
        let radar_rotation = -yaw; // SVG transform on the cone
        let front_center = head_relative_azimuths(yaw, 1.0)[2]; // "C"
        assert_eq!(front_center, radar_rotation); // same magnitude AND sign
        assert!(front_center < 0.0); // centre is now to the right, as expected for a left turn
    }
}
