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
}
