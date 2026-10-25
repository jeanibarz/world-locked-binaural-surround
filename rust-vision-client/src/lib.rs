//! Versioned wire protocol for the engine ↔ head-pose helper boundary (R-engine `vision-client`).
//!
//! The head-pose source (MediaPipe sidecar today; a Rust-ownable ONNX model after R-S1) talks to
//! the engine over this protocol only — NDJSON, one [`Envelope`] per line. Keeping the contract here
//! means the engine never depends on which model/runtime wins R-S1: swap the producer, keep the
//! wire. Carries **raw pose + capture timestamp** (so the engine's One-Euro/α-β predictor
//! compensates the true latency), confidence, control, and heartbeat.
//!
//! Versioning: `v` is the protocol's major version; a reader refuses an unknown major (fail loud),
//! but unknown *fields* are ignored (forward-compatible minor additions). This is the conformance
//! both ends test against.

use serde::{Deserialize, Serialize};

pub mod solve;

/// Protocol major version. Bump only on a breaking change; additive fields do not bump it.
pub const PROTOCOL_VERSION: u32 = 1;

/// One message on the wire (internally tagged by `type`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum VisionMsg {
    /// A head-pose sample. `capture_ns` is the camera-frame capture time (monotonic ns) — the
    /// engine needs it, not the receive time, to predict pose to the audio-render instant.
    Pose {
        seq: u64,
        capture_ns: u64,
        yaw_deg: f64,
        pitch_deg: f64,
        roll_deg: f64,
        confidence: f32,
    },
    /// Engine → helper control.
    Control { cmd: Control },
    /// Liveness; `sent_ns` is the sender's monotonic clock.
    Heartbeat { sent_ns: u64 },
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "cmd", rename_all = "snake_case")]
pub enum Control {
    Recenter,
    SelectCamera { index: u32 },
    Start,
    Stop,
}

/// Wire envelope: a protocol version plus the flattened message.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Envelope {
    pub v: u32,
    #[serde(flatten)]
    pub msg: VisionMsg,
}

#[derive(Debug)]
pub enum DecodeError {
    Json(serde_json::Error),
    /// The message's major version is not one this build speaks.
    UnsupportedVersion(u32),
}

impl std::fmt::Display for DecodeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            DecodeError::Json(e) => write!(f, "json: {e}"),
            DecodeError::UnsupportedVersion(v) => {
                write!(f, "unsupported protocol version {v} (this build speaks {PROTOCOL_VERSION})")
            }
        }
    }
}
impl std::error::Error for DecodeError {}

/// Encode a message as one NDJSON line (trailing `\n`), stamped with the current protocol version.
pub fn encode_line(msg: &VisionMsg) -> String {
    let env = Envelope { v: PROTOCOL_VERSION, msg: msg.clone() };
    // serialization of these plain types cannot fail.
    serde_json::to_string(&env).expect("vision_client envelope serialization") + "\n"
}

/// Decode one NDJSON line. Refuses an unknown major version; ignores unknown (minor) fields.
pub fn decode_line(line: &str) -> Result<VisionMsg, DecodeError> {
    let env: Envelope = serde_json::from_str(line.trim()).map_err(DecodeError::Json)?;
    if env.v != PROTOCOL_VERSION {
        return Err(DecodeError::UnsupportedVersion(env.v));
    }
    Ok(env.msg)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn roundtrip(m: VisionMsg) {
        assert_eq!(decode_line(&encode_line(&m)).unwrap(), m);
    }

    #[test]
    fn roundtrips_all_variants() {
        roundtrip(VisionMsg::Pose {
            seq: 42, capture_ns: 123_456_789, yaw_deg: -31.5, pitch_deg: 6.0, roll_deg: 2.25,
            confidence: 0.97,
        });
        roundtrip(VisionMsg::Control { cmd: Control::Recenter });
        roundtrip(VisionMsg::Control { cmd: Control::SelectCamera { index: 2 } });
        roundtrip(VisionMsg::Control { cmd: Control::Start });
        roundtrip(VisionMsg::Heartbeat { sent_ns: 999 });
    }

    #[test]
    fn encodes_one_ndjson_line() {
        let s = encode_line(&VisionMsg::Heartbeat { sent_ns: 1 });
        assert!(s.ends_with('\n'));
        assert_eq!(s.matches('\n').count(), 1);
        assert!(s.contains("\"v\":1") && s.contains("\"type\":\"heartbeat\""));
    }

    #[test]
    fn refuses_unknown_major_version() {
        let line = r#"{"v":999,"type":"heartbeat","sent_ns":1}"#;
        match decode_line(line) {
            Err(DecodeError::UnsupportedVersion(999)) => {}
            other => panic!("expected UnsupportedVersion(999), got {other:?}"),
        }
    }

    #[test]
    fn ignores_unknown_minor_fields() {
        // a future minor adds a field; a v1 reader must still accept the message (forward-compat).
        let line = r#"{"v":1,"type":"pose","seq":1,"capture_ns":10,"yaw_deg":1.0,"pitch_deg":2.0,"roll_deg":3.0,"confidence":0.5,"future_field":123}"#;
        let m = decode_line(line).unwrap();
        assert!(matches!(m, VisionMsg::Pose { seq: 1, .. }));
    }
}
