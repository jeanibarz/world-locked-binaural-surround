//! `control-plane` — the zero-Python cockpit server (RFC: standalone-distributable-and-rust-engine,
//! slice 4). An `axum` server that serves the existing web cockpit assets plus the live wire
//! protocol the Python `apps/cockpit_web.py` server speaks, so the browser cockpit runs unchanged
//! against a Rust engine:
//!
//! * `GET /events` — SSE: one `data: <json>\n\n` per engine tick; `: ping\n\n` when idle.
//! * `GET /camera` — MJPEG `multipart/x-mixed-replace; boundary=frame`.
//! * `GET /api/get_config` — the dropdown options + current values (JSON).
//! * `POST /api/<name>` — a control event; JSON arg list dispatched to the engine; replies `{}`.
//! * everything else — a static file from the cockpit asset dir.
//!
//! The wire protocol is frozen by a conformance test that runs the same requests against **both**
//! this server and the Python one (`tests/test_control_plane_conformance.py`). The engine drives it
//! through [`Cockpit`] ([`Cockpit::push_state`] / [`Cockpit::set_frame`] / [`Cockpit::set_config`]);
//! control events land in [`Cockpit::take_dispatched`].

use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use axum::body::Body;
use axum::extract::{Path, State};
use axum::http::{header, StatusCode};
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use bytes::Bytes;
use tokio::sync::{broadcast, watch};

pub mod settings;

const NO_STORE: &str = "no-store";

/// Content-Type for a cockpit asset by extension — matches the Python server's `_CTYPE` table.
fn content_type(path: &str) -> &'static str {
    match path.rsplit('.').next() {
        Some("html") => "text/html",
        Some("css") => "text/css",
        Some("js") => "application/javascript",
        Some("png") => "image/png",
        Some("svg") => "image/svg+xml",
        Some("ico") => "image/x-icon",
        _ => "application/octet-stream",
    }
}

/// Shared engine-facing state + client fan-out. Clone-free via `Arc`.
pub struct Cockpit {
    web_dir: PathBuf,
    config: Mutex<serde_json::Value>,
    state_tx: broadcast::Sender<String>,
    frame_tx: watch::Sender<Option<Arc<Vec<u8>>>>,
    // Keep one receiver alive so `watch::send` retains the latest frame even when no `/camera`
    // client is connected (a watch send is dropped if there are zero receivers).
    _frame_keep: watch::Receiver<Option<Arc<Vec<u8>>>>,
    dispatched: Mutex<Vec<(String, serde_json::Value)>>,
}

impl Cockpit {
    /// `web_dir` holds the cockpit assets (`index.html`, `cockpit.js`, icons); `config` is the
    /// initial `/api/get_config` payload.
    pub fn new(web_dir: impl Into<PathBuf>, config: serde_json::Value) -> Arc<Self> {
        let (state_tx, _) = broadcast::channel(64);
        let (frame_tx, frame_keep) = watch::channel(None);
        Arc::new(Self {
            web_dir: web_dir.into(),
            config: Mutex::new(config),
            state_tx,
            frame_tx,
            _frame_keep: frame_keep,
            dispatched: Mutex::new(Vec::new()),
        })
    }

    /// Push one engine-state JSON to all connected `/events` clients.
    pub fn push_state(&self, json_line: impl Into<String>) {
        let _ = self.state_tx.send(json_line.into());
    }
    /// Replace the latest camera JPEG served over `/camera`.
    pub fn set_frame(&self, jpeg: Vec<u8>) {
        let _ = self.frame_tx.send(Some(Arc::new(jpeg)));
    }
    /// Replace the `/api/get_config` payload.
    pub fn set_config(&self, config: serde_json::Value) {
        *self.config.lock().unwrap() = config;
    }
    /// Drain the control events received since the last call (for tests / the engine).
    pub fn take_dispatched(&self) -> Vec<(String, serde_json::Value)> {
        std::mem::take(&mut *self.dispatched.lock().unwrap())
    }
}

/// Serve the cockpit on `127.0.0.1:port` forever, owning a Tokio runtime — the engine spawns this on
/// a thread so it needs neither `axum` nor `tokio` directly. Prints `COCKPIT <url>` once bound.
pub fn serve_blocking(cockpit: Arc<Cockpit>, port: u16) {
    let rt = tokio::runtime::Builder::new_multi_thread().enable_all().build().expect("tokio runtime");
    rt.block_on(async move {
        let l = tokio::net::TcpListener::bind(("127.0.0.1", port)).await.expect("bind cockpit port");
        println!("COCKPIT http://127.0.0.1:{}", l.local_addr().unwrap().port());
        axum::serve(l, router(cockpit)).await.expect("cockpit serve");
    });
}

/// Build the router for a [`Cockpit`].
pub fn router(cockpit: Arc<Cockpit>) -> Router {
    Router::new()
        .route("/events", get(sse))
        .route("/camera", get(mjpeg))
        .route("/api/get_config", get(get_config))
        .route("/api/*name", post(api_post))
        .fallback(get(static_file))
        .with_state(cockpit)
}

/// `_send` equivalent: a body with Content-Type, Content-Length (axum adds it) and `Cache-Control:
/// no-store`, matching the Python server.
fn sent(status: StatusCode, ctype: &str, body: Vec<u8>) -> Response {
    Response::builder()
        .status(status)
        .header(header::CONTENT_TYPE, ctype)
        .header(header::CACHE_CONTROL, NO_STORE)
        .body(Body::from(body))
        .unwrap()
}

async fn get_config(State(c): State<Arc<Cockpit>>) -> Response {
    let body = serde_json::to_vec(&*c.config.lock().unwrap()).unwrap();
    sent(StatusCode::OK, "application/json", body)
}

async fn api_post(State(c): State<Arc<Cockpit>>, Path(name): Path<String>, body: Bytes) -> Response {
    // The body is a JSON arg list (default []); a non-list is wrapped to a 1-element list, exactly
    // like the Python server.
    let args: serde_json::Value = if body.is_empty() {
        serde_json::Value::Array(vec![])
    } else {
        match serde_json::from_slice::<serde_json::Value>(&body) {
            Ok(serde_json::Value::Array(a)) => serde_json::Value::Array(a),
            Ok(other) => serde_json::Value::Array(vec![other]),
            Err(_) => serde_json::Value::Array(vec![]),
        }
    };
    c.dispatched.lock().unwrap().push((name, args));
    sent(StatusCode::OK, "application/json", b"{}".to_vec())
}

async fn static_file(State(c): State<Arc<Cockpit>>, uri: axum::http::Uri) -> Response {
    let path = uri.path();
    let rel = if path == "/" || path.is_empty() { "index.html" } else { path.trim_start_matches('/') };
    // Reject path traversal: the resolved file must stay within web_dir.
    let candidate = c.web_dir.join(rel);
    let ok = candidate.canonicalize().ok().and_then(|abs| {
        c.web_dir.canonicalize().ok().map(|root| abs.starts_with(&root) && abs.is_file())
    });
    if ok != Some(true) {
        return sent(StatusCode::NOT_FOUND, "text/plain", b"not found".to_vec());
    }
    match std::fs::read(&candidate) {
        Ok(bytes) => sent(StatusCode::OK, content_type(rel), bytes),
        Err(_) => sent(StatusCode::NOT_FOUND, "text/plain", b"not found".to_vec()),
    }
}

async fn sse(State(c): State<Arc<Cockpit>>) -> Response {
    let mut rx = c.state_tx.subscribe();
    let stream = async_stream::stream! {
        loop {
            match tokio::time::timeout(Duration::from_secs(1), rx.recv()).await {
                Ok(Ok(msg)) => {
                    let mut line = Vec::with_capacity(msg.len() + 8);
                    line.extend_from_slice(b"data: ");
                    line.extend_from_slice(msg.as_bytes());
                    line.extend_from_slice(b"\n\n");
                    yield Ok::<Bytes, std::io::Error>(Bytes::from(line));
                }
                Ok(Err(broadcast::error::RecvError::Lagged(_))) => continue,
                Ok(Err(broadcast::error::RecvError::Closed)) => break,
                Err(_) => yield Ok(Bytes::from_static(b": ping\n\n")), // idle keep-alive
            }
        }
    };
    Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "text/event-stream")
        .header(header::CACHE_CONTROL, NO_STORE)
        .header(header::CONNECTION, "keep-alive")
        .body(Body::from_stream(stream))
        .unwrap()
}

async fn mjpeg(State(c): State<Arc<Cockpit>>) -> Response {
    let rx = c.frame_tx.subscribe();
    let stream = async_stream::stream! {
        // Poll the latest frame every 20 ms and emit it when it changes — matching the Python
        // server's `sleep(0.02)` poll loop. (A simple, backpressure-free fan-out; the newest frame
        // always wins, dropping skipped ones, which is correct for a live preview.)
        let mut last: Option<Arc<Vec<u8>>> = None;
        let mut tick = tokio::time::interval(Duration::from_millis(20));
        loop {
            tick.tick().await;
            let frame = rx.borrow().clone();
            if let Some(f) = frame {
                let is_new = match &last { Some(l) => !Arc::ptr_eq(l, &f), None => true };
                if is_new {
                    last = Some(f.clone());
                    let mut buf = Vec::with_capacity(f.len() + 64);
                    buf.extend_from_slice(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ");
                    buf.extend_from_slice(f.len().to_string().as_bytes());
                    buf.extend_from_slice(b"\r\n\r\n");
                    buf.extend_from_slice(&f);
                    buf.extend_from_slice(b"\r\n");
                    yield Ok::<Bytes, std::io::Error>(Bytes::from(buf));
                }
            }
        }
    };
    Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "multipart/x-mixed-replace; boundary=frame")
        .header(header::CACHE_CONTROL, NO_STORE)
        .body(Body::from_stream(stream))
        .unwrap()
}
