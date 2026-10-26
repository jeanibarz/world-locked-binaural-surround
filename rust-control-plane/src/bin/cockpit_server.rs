//! `cockpit_server` — run the Rust control-plane standalone. Used by the wire-protocol conformance
//! test (fed canned fixtures identical to the Python server) and as the real engine's server.
//!
//! Args: `--web-dir <dir> [--port N] [--config <json>] [--state <json>] [--frame <jpeg>]
//!        [--state-interval-ms N]`. Prints `LISTENING <port>` once bound (so a test can read the
//! ephemeral port), then serves until killed.

use std::sync::Arc;
use std::time::Duration;

use control_plane::{router, Cockpit};

fn arg(args: &[String], key: &str) -> Option<String> {
    args.iter().position(|a| a == key).and_then(|i| args.get(i + 1)).cloned()
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args().collect();
    let web_dir = arg(&args, "--web-dir").expect("--web-dir required");
    let port: u16 = arg(&args, "--port").and_then(|s| s.parse().ok()).unwrap_or(0);
    let config = arg(&args, "--config")
        .map(|p| serde_json::from_slice(&std::fs::read(p).expect("read config")).expect("parse config"))
        .unwrap_or_else(|| serde_json::json!({}));
    let state = arg(&args, "--state").map(|p| std::fs::read_to_string(p).expect("read state"));
    let frame = arg(&args, "--frame").map(|p| std::fs::read(p).expect("read frame"));
    let interval_ms: u64 = arg(&args, "--state-interval-ms").and_then(|s| s.parse().ok()).unwrap_or(200);

    let cockpit = Cockpit::new(web_dir, config);
    if let Some(f) = frame {
        cockpit.set_frame(f);
    }
    if let Some(s) = state {
        // Push the fixture state on a timer so late-connecting SSE clients still receive it.
        let c = Arc::clone(&cockpit);
        tokio::spawn(async move {
            let mut tick = tokio::time::interval(Duration::from_millis(interval_ms.max(1)));
            loop {
                tick.tick().await;
                c.push_state(s.clone());
            }
        });
    }

    let listener = tokio::net::TcpListener::bind(("127.0.0.1", port)).await?;
    println!("LISTENING {}", listener.local_addr()?.port());
    axum::serve(listener, router(cockpit)).await?;
    Ok(())
}
