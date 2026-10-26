//! filterpack v1 loader (M-D1) — the shared engine loader.
//!
//! This MUST read a pack byte-identically to the Python reference loader
//! (`src/binaural_headtracker/filterpack.py`): same validation, same integrity check, same
//! `canonical_digest` byte layout. The M-D1 gate is that both produce the same sha256 on the same
//! fixtures. Dependency surface is deliberately tiny (serde_json + sha2 + hound) — no SOFA/HDF5.

use std::fs;
use std::path::Path;

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use sha2::{Digest, Sha256};

const FILTERPACK_VERSION: u8 = 1;

const REQUIRED: &[&str] = &[
    "filterpack_version", "name", "samplerate", "ir_length", "comp", "convention", "gain",
    "layout", "integrity",
];
const REQUIRED_CONVENTION: &[&str] =
    &["azimuth_zero", "azimuth_positive", "angle_units", "yaw_sign", "pitch_sign", "roll_sign"];

struct Speaker {
    channel: String,
    azimuth_deg: Option<f64>,
    left: Vec<f64>,  // left-ear IR
    right: Vec<f64>, // right-ear IR
}

fn err<T>(msg: impl Into<String>) -> Result<T, String> {
    Err(msg.into())
}

/// Read a 2-channel WAV as (left_ear, right_ear) f64 in [-1, 1]. int16 → /32768.0, or float32.
/// Matches the Python loader exactly (same divisor, same interleave → same f64 bit patterns).
fn read_stereo_wav(path: &Path, expect_len: usize) -> Result<(Vec<f64>, Vec<f64>), String> {
    let name = path.file_name().and_then(|s| s.to_str()).unwrap_or("<wav>");
    let reader = hound::WavReader::open(path).map_err(|e| format!("{name}: {e}"))?;
    let spec = reader.spec();
    if spec.channels != 2 {
        return err(format!("{name}: expected 2 channels (L-ear/R-ear), got {}", spec.channels));
    }
    let interleaved: Vec<f64> = match (spec.sample_format, spec.bits_per_sample) {
        (hound::SampleFormat::Int, 16) => reader
            .into_samples::<i16>()
            .map(|s| s.map(|v| v as f64 / 32768.0).map_err(|e| e.to_string()))
            .collect::<Result<_, _>>()?,
        (hound::SampleFormat::Float, 32) => reader
            .into_samples::<f32>()
            .map(|s| s.map(|v| v as f64).map_err(|e| e.to_string()))
            .collect::<Result<_, _>>()?,
        (_, b) => return err(format!("{name}: unsupported sample width {b} bits")),
    };
    let frames = interleaved.len() / 2;
    if frames != expect_len {
        return err(format!("{name}: ir_length {frames} != manifest {expect_len}"));
    }
    let mut left = Vec::with_capacity(frames);
    let mut right = Vec::with_capacity(frames);
    for f in 0..frames {
        left.push(interleaved[2 * f]);
        right.push(interleaved[2 * f + 1]);
    }
    Ok((left, right))
}

fn sha256_hex(bytes: &[u8]) -> String {
    let mut h = Sha256::new();
    h.update(bytes);
    hex(&h.finalize())
}

fn hex(bytes: &[u8]) -> String {
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        s.push_str(&format!("{b:02x}"));
    }
    s
}

fn load(pack_dir: &str) -> Result<(u32, usize, String, Vec<Speaker>), String> {
    let d = Path::new(pack_dir);
    let man_path = d.join("manifest.json");
    if !man_path.is_file() {
        return err(format!("no manifest.json in {pack_dir}"));
    }
    let text = fs::read_to_string(&man_path).map_err(|e| format!("manifest.json: {e}"))?;
    let m: serde_json::Value =
        serde_json::from_str(&text).map_err(|e| format!("manifest.json is not valid JSON: {e}"))?;

    for k in REQUIRED {
        if m.get(k).is_none() {
            return err(format!("manifest missing required key: {k:?}"));
        }
    }
    if m["filterpack_version"].as_u64() != Some(FILTERPACK_VERSION as u64) {
        return err(format!(
            "unsupported filterpack_version {} (this loader is v{FILTERPACK_VERSION})",
            m["filterpack_version"]
        ));
    }
    for k in REQUIRED_CONVENTION {
        if m["convention"].get(k).is_none() {
            return err(format!("convention missing required key: {k:?} (packs must be unambiguous)"));
        }
    }

    let samplerate = m["samplerate"].as_u64().ok_or("samplerate not an integer")? as u32;
    let ir_length = m["ir_length"].as_u64().ok_or("ir_length not an integer")? as usize;
    let comp = m["comp"].as_str().ok_or("comp not a string")?.to_string();
    let layout = &m["layout"];

    let integ = &m["integrity"];
    if integ["algo"].as_str() != Some("sha256") {
        return err(format!("unsupported integrity algo {:?}", integ["algo"]));
    }
    let files = integ["files"].as_object().ok_or("integrity.files missing")?;

    let channels = layout["channels"].as_array().ok_or("layout.channels missing")?;
    let speakers_map = &layout["speakers"];
    let mut speakers = Vec::with_capacity(channels.len());
    for ch_v in channels {
        let ch = ch_v.as_str().ok_or("layout.channels entry not a string")?;
        let spec = speakers_map.get(ch).ok_or_else(|| {
            format!("layout.channels lists {ch:?} but layout.speakers has no entry")
        })?;
        let ir_file = spec["ir"].as_str().ok_or("speaker.ir missing")?;
        let want_hash = files
            .get(ir_file)
            .and_then(|v| v.as_str())
            .ok_or_else(|| format!("{ir_file}: no integrity hash recorded"))?;
        let fpath = d.join(ir_file);
        if !fpath.is_file() {
            return err(format!("{ir_file}: file missing"));
        }
        let got = sha256_hex(&fs::read(&fpath).map_err(|e| format!("{ir_file}: {e}"))?);
        if got != want_hash {
            return err(format!("{ir_file}: sha256 mismatch (pack corrupt or tampered)"));
        }
        let (left, right) = read_stereo_wav(&fpath, ir_length)?;
        let azimuth_deg = match &spec["azimuth_deg"] {
            serde_json::Value::Null => None,
            v => Some(v.as_f64().ok_or("azimuth_deg not a number")?),
        };
        speakers.push(Speaker { channel: ch.to_string(), azimuth_deg, left, right });
    }
    Ok((samplerate, ir_length, comp, speakers))
}

/// The cross-language canonical byte layout (see the Python loader's docstring). Must match exactly.
fn canonical_digest(
    samplerate: u32,
    ir_length: usize,
    comp: &str,
    speakers: &[Speaker],
) -> String {
    let mut h = Sha256::new();
    h.update(b"FPAK");
    h.update([FILTERPACK_VERSION]);
    h.update((samplerate).to_be_bytes());
    h.update((ir_length as u32).to_be_bytes());
    h.update((speakers.len() as u32).to_be_bytes());

    let put_str = |h: &mut Sha256, s: &str| {
        h.update((s.len() as u16).to_be_bytes());
        h.update(s.as_bytes());
    };
    put_str(&mut h, comp);
    for sp in speakers {
        put_str(&mut h, &sp.channel);
        match sp.azimuth_deg {
            None => h.update([0u8]),
            Some(az) => {
                h.update([1u8]);
                h.update(az.to_be_bytes());
            }
        }
        for v in &sp.left {
            h.update(v.to_be_bytes());
        }
        for v in &sp.right {
            h.update(v.to_be_bytes());
        }
    }
    hex(&h.finalize())
}

/// Load a pack and return its canonical digest — the Python binding compares this to its own.
#[pyfunction]
fn canonical_digest_of(pack_dir: &str) -> PyResult<String> {
    let (sr, ir_len, comp, speakers) = load(pack_dir).map_err(PyValueError::new_err)?;
    Ok(canonical_digest(sr, ir_len, &comp, &speakers))
}

#[pymodule]
fn filterpack_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(canonical_digest_of, m)?)?;
    Ok(())
}
