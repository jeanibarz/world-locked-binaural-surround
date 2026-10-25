//! Port-parity: the Rust Kabsch solve reproduces the Python spike's pose on identical landmarks.
//!
//! Fixture `kabsch_fixture.json` holds the 20-point canonical rigid subset + 8 real AFLW2000 cases
//! (detected landmark xyz + the (yaw,pitch,roll) the Python Umeyama produced). Rust `solve_pose`
//! must match each to numerical precision — proving the port, independent of the already-validated
//! accuracy-vs-MediaPipe (~3.9/1.4/0.8°).

use serde::Deserialize;
use vision_client::solve::solve_pose;

#[derive(Deserialize)]
struct Case {
    landmarks: Vec<[f64; 3]>,
    expect: [f64; 3], // yaw, pitch, roll (deg) from the Python reference
}
#[derive(Deserialize)]
struct Fixture {
    canonical_rigid: Vec<[f64; 3]>,
    cases: Vec<Case>,
}

#[test]
fn rust_kabsch_matches_python_reference() {
    let raw = std::fs::read_to_string(
        concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/kabsch_fixture.json"),
    )
    .expect("read fixture");
    let fx: Fixture = serde_json::from_str(&raw).expect("parse fixture");
    assert_eq!(fx.canonical_rigid.len(), 20);
    assert!(fx.cases.len() >= 5);

    let mut max_err = 0.0f64;
    for (i, c) in fx.cases.iter().enumerate() {
        assert_eq!(c.landmarks.len(), 20);
        let (yaw, pitch, roll) = solve_pose(&fx.canonical_rigid, &c.landmarks);
        for (got, want) in [yaw, pitch, roll].iter().zip(c.expect.iter()) {
            let e = (got - want).abs();
            max_err = max_err.max(e);
            assert!(
                e < 1e-3,
                "case {i}: axis err {e:.2e}° exceeds 1e-3 (got {got:.4}, want {want:.4})"
            );
        }
    }
    eprintln!("max |Rust - Python| across 8 cases × 3 axes: {max_err:.2e}°");
}
