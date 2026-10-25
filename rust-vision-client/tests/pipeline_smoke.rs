//! End-to-end pipeline smoke test, gated on host assets (ONNX nets + canonical model + a face image)
//! so CI without the assets skips cleanly. When the env vars are set it runs the real
//! BlazeFace→FaceMesh→Kabsch path and asserts a detected face with a finite, in-plausible-range pose.
//!
//! The full numeric guards (Guard-1 landmark parity ≈1px, Guard-2 GT-RMS ≤ MediaPipe+2°/axis) are
//! measured against the MediaPipe reference over AFLW2000 — see the PR's measured gate — not in this
//! unit test, which only proves the wired pipeline runs and produces sane output.
//!
//! Run (host): WLBS_ONNX_DIR=/mnt/c/Users/ibarz/facemesh_extract \
//!   WLBS_CANONICAL=/mnt/c/Users/ibarz/canonical_face_model.obj \
//!   WLBS_FACE_IMAGE=/mnt/c/Users/ibarz/aflw_gt/AFLW2000/image00013.jpg \
//!   cargo test --features inference --test pipeline_smoke -- --nocapture

#![cfg(feature = "inference")]

use vision_client::pipeline::{decode_image, Pipeline};

#[test]
fn pipeline_detects_face_and_solves_pose() {
    let (onnx, canon, imgpath) = match (
        std::env::var("WLBS_ONNX_DIR"),
        std::env::var("WLBS_CANONICAL"),
        std::env::var("WLBS_FACE_IMAGE"),
    ) {
        (Ok(a), Ok(b), Ok(c)) => (a, b, c),
        _ => {
            eprintln!("skipping: set WLBS_ONNX_DIR, WLBS_CANONICAL, WLBS_FACE_IMAGE to run");
            return;
        }
    };
    let mut pipe = Pipeline::new(&onnx, &canon).expect("load pipeline");
    let bytes = std::fs::read(&imgpath).expect("read face image");
    let img = decode_image(&bytes).expect("decode face image");
    let est = pipe.estimate(&img).expect("inference ok").expect("a face was detected");

    assert_eq!(est.landmarks.len(), 478);
    assert!(est.score >= 0.5, "detector score below threshold: {}", est.score);
    let (yaw, pitch, roll) = est.pose;
    for (name, v) in [("yaw", yaw), ("pitch", pitch), ("roll", roll)] {
        assert!(v.is_finite(), "{name} not finite");
        assert!(v.abs() <= 180.0, "{name} out of range: {v}");
    }
    // landmarks are normalized into [0,1]-ish image coords (small over/underflow at frame edges ok)
    for l in &est.landmarks {
        assert!(l[0] > -0.5 && l[0] < 1.5 && l[1] > -0.5 && l[1] < 1.5, "landmark wildly off-frame");
    }
    eprintln!("pose: yaw={yaw:.2} pitch={pitch:.2} roll={roll:.2} score={:.2}", est.score);
}
