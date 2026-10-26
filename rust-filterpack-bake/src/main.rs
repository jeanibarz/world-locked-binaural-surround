//! Zero-Python filterpack baker: the owner's baked 7.1 BRIR set (`<ch>_L.wav` / `<ch>_R.wav`) →
//! a v1 filter pack (per-speaker int16 stereo WAV + `manifest.json`), byte-for-byte equivalent (by
//! the loader's content digest) to the Python `filterpack_bake.bake_baked_brir`. This is the
//! Track-S core that removes Python from owner-pack baking; the full SOFA/HDF5 + CTF/DF studio is a
//! separate, larger effort (and its Python form is an RFC-sanctioned interim).
//!
//! Usage: filterpack_bake <baked_brir_dir> <out_dir> [--name NAME] [--comp ctf|df]

use std::path::{Path, PathBuf};

use sha2::{Digest, Sha256};

const FILTERPACK_VERSION: u64 = 1;
// WASAPI 7.1 interleave order; each channel has <ch>_L.wav / <ch>_R.wav.
const CHANNELS_7P1: [&str; 8] = ["L", "R", "C", "LFE", "RL", "RR", "SL", "SR"];

fn speaker_azimuth(ch: &str) -> Option<f64> {
    match ch {
        "L" => Some(30.0),
        "R" => Some(-30.0),
        "C" => Some(0.0),
        "SL" => Some(110.0),
        "SR" => Some(-110.0),
        "RL" => Some(150.0),
        "RR" => Some(-150.0),
        _ => None, // LFE (non-directional)
    }
}

/// Read a mono IR WAV → f64 in [-1,1] (int → /max, float as-is); channel 0 if multichannel.
fn read_mono(path: &Path) -> Result<(u32, Vec<f64>), String> {
    let mut r = hound::WavReader::open(path).map_err(|e| format!("{}: {e}", path.display()))?;
    let spec = r.spec();
    let ch = spec.channels.max(1) as usize;
    let take0 = |v: Vec<f64>| v.into_iter().step_by(ch).collect::<Vec<f64>>();
    let samples = match spec.sample_format {
        hound::SampleFormat::Float => take0(r.samples::<f32>().map(|s| s.unwrap_or(0.0) as f64).collect()),
        hound::SampleFormat::Int => {
            let max = (1i64 << (spec.bits_per_sample - 1)) as f64;
            take0(r.samples::<i32>().map(|s| s.unwrap_or(0) as f64 / max).collect())
        }
    };
    Ok((spec.sample_rate, samples))
}

/// Write a 2-ch int16 WAV (ch0=left ear, ch1=right ear) and return its sha256 hex. The int16
/// conversion matches the Python baker exactly: clip(round(x*32767), -32768, 32767).
fn write_int16_wav(path: &Path, left: &[f64], right: &[f64], sr: u32) -> Result<String, String> {
    let spec = hound::WavSpec {
        channels: 2,
        sample_rate: sr,
        bits_per_sample: 16,
        sample_format: hound::SampleFormat::Int,
    };
    let mut w = hound::WavWriter::create(path, spec).map_err(|e| format!("{}: {e}", path.display()))?;
    let to_i16 = |x: f64| ((x * 32767.0).round()).clamp(-32768.0, 32767.0) as i16;
    for (l, r) in left.iter().zip(right.iter()) {
        w.write_sample(to_i16(*l)).map_err(|e| e.to_string())?;
        w.write_sample(to_i16(*r)).map_err(|e| e.to_string())?;
    }
    w.finalize().map_err(|e| e.to_string())?;
    let bytes = std::fs::read(path).map_err(|e| e.to_string())?;
    Ok(hex(&Sha256::digest(&bytes)))
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

/// Bake a baked-BRIR dir into `out_dir` as a v1 filterpack. Returns the manifest path.
fn bake(brir_dir: &Path, out_dir: &Path, name: &str, comp: &str) -> Result<PathBuf, String> {
    std::fs::create_dir_all(out_dir).map_err(|e| e.to_string())?;
    // Load every channel's L/R mono IR; pad to a common length (longest).
    let mut sr = 0u32;
    let mut chans: Vec<(String, Vec<f64>, Vec<f64>)> = Vec::new();
    for ch in CHANNELS_7P1 {
        let (srl, l) = read_mono(&brir_dir.join(format!("{ch}_L.wav")))?;
        let (srr, r) = read_mono(&brir_dir.join(format!("{ch}_R.wav")))?;
        if sr == 0 {
            sr = srl;
        }
        if srl != sr || srr != sr {
            return Err(format!("{ch}: samplerate mismatch"));
        }
        chans.push((ch.to_string(), l, r));
    }
    let ir_len = chans.iter().flat_map(|(_, l, r)| [l.len(), r.len()]).max().unwrap_or(0);
    if ir_len == 0 {
        return Err("empty BRIR set".into());
    }

    let mut files = serde_json::Map::new();
    let mut channels_arr = Vec::new();
    let mut speakers = serde_json::Map::new();
    for (ch, mut l, mut r) in chans {
        l.resize(ir_len, 0.0);
        r.resize(ir_len, 0.0);
        let fname = format!("{ch}.wav");
        let hash = write_int16_wav(&out_dir.join(&fname), &l, &r, sr)?;
        files.insert(fname.clone(), serde_json::Value::String(hash));
        channels_arr.push(serde_json::Value::String(ch.clone()));
        let az = match speaker_azimuth(&ch) {
            Some(a) => serde_json::json!(a),
            None => serde_json::Value::Null,
        };
        speakers.insert(ch.clone(), serde_json::json!({"azimuth_deg": az, "ir": fname}));
    }
    let lfe = if CHANNELS_7P1.contains(&"LFE") {
        serde_json::json!({"channel": "LFE", "mode": "brir"})
    } else {
        serde_json::Value::Null
    };

    let manifest = serde_json::json!({
        "filterpack_version": FILTERPACK_VERSION,
        "name": name,
        "provenance": {"source": "baker", "subject": "", "license": "owner"},
        "samplerate": sr,
        "ir_length": ir_len,
        "comp": comp,
        "convention": {
            "azimuth_zero": "front", "azimuth_positive": "counterclockwise",
            "angle_units": "degrees", "yaw_sign": 1, "pitch_sign": 1, "roll_sign": 1
        },
        "gain": {"integrated_lufs": serde_json::Value::Null, "peak_dbfs": serde_json::Value::Null},
        "layout": {"channels": channels_arr, "lfe": lfe, "speakers": speakers},
        "grid": serde_json::Value::Null,
        "integrity": {"algo": "sha256", "files": files}
    });
    let man_path = out_dir.join("manifest.json");
    std::fs::write(&man_path, serde_json::to_string_pretty(&manifest).unwrap())
        .map_err(|e| e.to_string())?;
    Ok(man_path)
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let a: Vec<String> = std::env::args().collect();
    let brir_dir = a.get(1).ok_or("usage: filterpack_bake <baked_brir_dir> <out_dir> [--name N] [--comp ctf|df]")?;
    let out_dir = a.get(2).ok_or("missing <out_dir>")?;
    let name = a.iter().position(|x| x == "--name").and_then(|i| a.get(i + 1)).cloned().unwrap_or_else(|| "mine".into());
    let comp = a.iter().position(|x| x == "--comp").and_then(|i| a.get(i + 1)).cloned().unwrap_or_else(|| "ctf".into());
    let man = bake(Path::new(brir_dir), Path::new(out_dir), &name, &comp)?;
    println!("baked -> {}", man.display());
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn int16_round_trips_exactly() {
        // write a known ramp, read it back, confirm int16 == clip(round(x*32767)).
        let dir = std::env::temp_dir().join(format!("fpbake_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let p = dir.join("t.wav");
        let left = vec![0.0, 0.5, -0.5, 1.0, -1.0];
        let right = vec![0.25, -0.25, 0.0, 0.999, -0.999];
        write_int16_wav(&p, &left, &right, 48000).unwrap();
        let mut r = hound::WavReader::open(&p).unwrap();
        let s: Vec<i16> = r.samples::<i16>().map(|x| x.unwrap()).collect();
        assert_eq!(s[0], 0);
        assert_eq!(s[2], (0.5f64 * 32767.0).round() as i16); // left[1]
        assert_eq!(s[6], (1.0f64 * 32767.0).round() as i16); // left[3] = 32767
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn bakes_a_pack_with_manifest_and_hashes() {
        let dir = std::env::temp_dir().join(format!("fpbake_pack_{}", std::process::id()));
        let src = dir.join("brir");
        std::fs::create_dir_all(&src).unwrap();
        let spec = hound::WavSpec { channels: 1, sample_rate: 48000, bits_per_sample: 32, sample_format: hound::SampleFormat::Float };
        for ch in CHANNELS_7P1 {
            for ear in ["L", "R"] {
                let mut w = hound::WavWriter::create(src.join(format!("{ch}_{ear}.wav")), spec).unwrap();
                for i in 0..64 { w.write_sample((i as f32 / 64.0) * 0.1).unwrap(); }
                w.finalize().unwrap();
            }
        }
        let out = dir.join("pack");
        bake(&src, &out, "test", "ctf").unwrap();
        let man: serde_json::Value = serde_json::from_slice(&std::fs::read(out.join("manifest.json")).unwrap()).unwrap();
        assert_eq!(man["filterpack_version"], 1);
        assert_eq!(man["ir_length"], 64);
        assert_eq!(man["layout"]["speakers"]["L"]["azimuth_deg"], 30.0);
        assert!(man["layout"]["speakers"]["LFE"]["azimuth_deg"].is_null());
        // every integrity hash matches its file
        let files = man["integrity"]["files"].as_object().unwrap();
        for (fname, h) in files {
            let got = hex(&Sha256::digest(std::fs::read(out.join(fname)).unwrap()));
            assert_eq!(&got, h.as_str().unwrap(), "{fname} hash");
        }
        let _ = std::fs::remove_dir_all(&dir);
    }
}
