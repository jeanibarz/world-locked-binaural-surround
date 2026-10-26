//! Run the Rust-native head-pose pipeline over a directory of images and emit per-image
//! pose + landmarks as JSON — the artifact the guard-1 (landmark parity) and guard-2 (pose parity)
//! harness diffs against the MediaPipe reference.
//!
//! Usage:
//!   pose_dump --onnx-dir <dir> --canonical <obj> --images <dir> --out <json> [--limit N]

use std::io::Write;

use serde::Serialize;
use vision_client::pipeline::{decode_image, Pipeline};

#[derive(Serialize)]
struct Record {
    file: String,
    /// [yaw, pitch, roll] degrees, or null if no face.
    pose: Option<[f64; 3]>,
    score: Option<f32>,
    /// 478×3 NormalizedLandmarks, or empty if no face. Omitted unless --landmarks.
    #[serde(skip_serializing_if = "Vec::is_empty")]
    landmarks: Vec<[f64; 3]>,
}

fn arg(args: &[String], key: &str) -> Option<String> {
    args.iter().position(|a| a == key).and_then(|i| args.get(i + 1)).cloned()
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args().collect();
    let onnx_dir = arg(&args, "--onnx-dir").unwrap_or_else(|| "/mnt/c/Users/ibarz/facemesh_extract".into());
    let canonical =
        arg(&args, "--canonical").unwrap_or_else(|| "/mnt/c/Users/ibarz/canonical_face_model.obj".into());
    let images = arg(&args, "--images").expect("--images <dir> required");
    let out = arg(&args, "--out").unwrap_or_else(|| "rust_pose_dump.json".into());
    let limit: usize = arg(&args, "--limit").map(|s| s.parse().unwrap()).unwrap_or(usize::MAX);
    let want_landmarks = args.iter().any(|a| a == "--landmarks");

    let mut pipe = Pipeline::new(&onnx_dir, &canonical)?;

    // collect images (jpg/png), sorted for determinism
    let mut paths: Vec<_> = std::fs::read_dir(&images)?
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| {
            matches!(
                p.extension().and_then(|e| e.to_str()).map(|e| e.to_ascii_lowercase()).as_deref(),
                Some("jpg") | Some("jpeg") | Some("png")
            )
        })
        .collect();
    paths.sort();

    let mut records = Vec::new();
    let (mut ok, mut miss) = (0usize, 0usize);
    for p in paths.into_iter().take(limit) {
        let bytes = std::fs::read(&p)?;
        let img = match decode_image(&bytes) {
            Ok(i) => i,
            Err(e) => {
                eprintln!("decode {p:?}: {e}");
                continue;
            }
        };
        let name = p.file_name().unwrap().to_string_lossy().to_string();
        match pipe.estimate(&img)? {
            Some(est) => {
                ok += 1;
                records.push(Record {
                    file: name,
                    pose: Some([est.pose.0, est.pose.1, est.pose.2]),
                    score: Some(est.score),
                    landmarks: if want_landmarks { est.landmarks } else { Vec::new() },
                });
            }
            None => {
                miss += 1;
                records.push(Record { file: name, pose: None, score: None, landmarks: Vec::new() });
            }
        }
    }

    let mut f = std::fs::File::create(&out)?;
    f.write_all(serde_json::to_string(&records)?.as_bytes())?;
    eprintln!("wrote {out}: {ok} faces, {miss} misses");
    Ok(())
}
