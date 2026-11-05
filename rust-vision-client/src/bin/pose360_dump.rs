//! Run the full-range head-pose backend (BlazeFace → 6DRepNet360 → full-circle azimuth) over a
//! directory of images and emit `file,azimuth` CSV — the artifact used to parity-check the Rust port
//! against the Python offline trajectory (journal 91). CPU execution provider is fine (the pose is
//! EP-independent); the azimuth is what we compare.
//!
//! Usage: pose360_dump --onnx-dir <dir with facedet.onnx + sixdrepnet360.onnx> --images <dir>

use vision_client::headpose360::Pose360Pipeline;
use vision_client::pipeline::decode_image;

fn arg(args: &[String], key: &str) -> Option<String> {
    args.iter().position(|a| a == key).and_then(|i| args.get(i + 1)).cloned()
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args().collect();
    let onnx_dir = arg(&args, "--onnx-dir").expect("--onnx-dir required");
    let images = arg(&args, "--images").expect("--images <dir> required");
    let mut pipe = Pose360Pipeline::new(&onnx_dir)?;
    // These images are independent (a parity bag, not a video), so detect-and-track must be OFF —
    // a miss must stay a miss, never re-use the previous image's box.
    pipe.set_track_misses(0);

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

    println!("file,azimuth,pitch,roll,score");
    let (mut ok, mut miss) = (0usize, 0usize);
    for p in paths {
        let name = p.file_name().unwrap().to_string_lossy().to_string();
        let img = decode_image(&std::fs::read(&p)?)?;
        match pipe.estimate(&img)? {
            Some(e) => {
                ok += 1;
                println!("{name},{:.1},{:.1},{:.1},{:.3}", e.azimuth, e.pitch, e.roll, e.score);
            }
            None => {
                miss += 1;
                println!("{name},,,,");
            }
        }
    }
    eprintln!("pose360_dump: {ok} faces, {miss} misses");
    Ok(())
}
