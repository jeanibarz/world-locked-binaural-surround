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

use std::collections::VecDeque;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use axum::body::Body;
use axum::extract::{DefaultBodyLimit, Path, Request, State};
use axum::http::{header, Method, StatusCode};
use axum::middleware::{self, Next};
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use bytes::Bytes;
use tokio::sync::{broadcast, watch};

pub mod settings;

const NO_STORE: &str = "no-store";
/// Max queued, undrained control events; the oldest is dropped past this.
const MAX_DISPATCHED: usize = 1024;
/// Max `/api` request body. The cockpit sends tiny JSON arg lists.
const MAX_API_BODY: usize = 64 * 1024;

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

/// A camera preview frame paired with the FaceMesh overlay computed from that SAME captured frame.
/// Serving them together over `/preview` lets the cockpit draw the picture and the landmark dots
/// from one frame, so the dots can't run ahead of the picture (the two used to ride separate paths:
/// dots over SSE+canvas, picture over the slower MJPEG `<img>`).
pub struct PreviewFrame {
    /// Baseline JPEG of the (un-mirrored) frame.
    pub jpeg: Vec<u8>,
    /// Overlay JSON fragment for this frame — `{"lm":[…],"box":[…]}` when a face was found this
    /// frame, or `{}` when not. Emitted verbatim inside the `/preview` event.
    pub overlay: String,
}

/// Shared engine-facing state + client fan-out. Clone-free via `Arc`.
pub struct Cockpit {
    web_dir: PathBuf,
    config: Mutex<serde_json::Value>,
    state_tx: broadcast::Sender<String>,
    frame_tx: watch::Sender<Option<Arc<PreviewFrame>>>,
    // Keep one receiver alive so `watch::send` retains the latest frame even when no `/camera` or
    // `/preview` client is connected (a watch send is dropped if there are zero receivers).
    _frame_keep: watch::Receiver<Option<Arc<PreviewFrame>>>,
    dispatched: Mutex<VecDeque<(String, serde_json::Value)>>,
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
            dispatched: Mutex::new(VecDeque::new()),
        })
    }

    /// Push one engine-state JSON to all connected `/events` clients.
    pub fn push_state(&self, json_line: impl Into<String>) {
        let _ = self.state_tx.send(json_line.into());
    }
    /// Replace the latest preview frame (JPEG + the overlay from the same frame), served over
    /// `/preview` (picture + dots, paired) and `/camera` (the JPEG alone, MJPEG).
    pub fn set_preview(&self, jpeg: Vec<u8>, overlay: String) {
        let _ = self.frame_tx.send(Some(Arc::new(PreviewFrame { jpeg, overlay })));
    }
    /// Replace the latest camera JPEG with no overlay (back-compat / preview with no face data).
    pub fn set_frame(&self, jpeg: Vec<u8>) {
        self.set_preview(jpeg, "{}".into());
    }
    /// Replace the `/api/get_config` payload.
    pub fn set_config(&self, config: serde_json::Value) {
        *self.config.lock().unwrap() = config;
    }
    /// Drain the control events received since the last call (for tests / the engine).
    pub fn take_dispatched(&self) -> Vec<(String, serde_json::Value)> {
        std::mem::take(&mut *self.dispatched.lock().unwrap()).into()
    }

    /// Queue a control event, bounded: a consecutive repeat of an idempotent setter (`set_*` /
    /// `window_size`) replaces the previous one (only the latest value matters), and past
    /// `MAX_DISPATCHED` entries the oldest is dropped, so an undrained queue can't grow forever.
    fn enqueue(&self, name: String, args: serde_json::Value) {
        let mut q = self.dispatched.lock().unwrap();
        let coalescable = name.starts_with("set_") || name == "window_size";
        if coalescable && q.back().map_or(false, |(n, _)| *n == name) {
            q.pop_back();
        }
        if q.len() >= MAX_DISPATCHED {
            q.pop_front();
        }
        q.push_back((name, args));
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
        .route("/preview", get(preview))
        .route("/api/get_config", get(get_config))
        .route("/api/version", get(version))
        .route("/api/*name", post(api_post).layer(DefaultBodyLimit::max(MAX_API_BODY)))
        .fallback(get(static_file))
        .layer(middleware::from_fn(guard))
        .with_state(cockpit)
}

/// Host name part of a `Host` header value (strips the port; handles `[::1]:port`).
fn host_name(host: &str) -> &str {
    if let Some(rest) = host.strip_prefix('[') {
        return rest.split(']').next().unwrap_or("");
    }
    host.rsplit_once(':').map_or(host, |(h, _)| h)
}

/// Loopback-only guard for every route. Rejects a `Host` that isn't `127.0.0.1`/`localhost`/`[::1]`
/// (DNS rebinding: a hostile page rebinding its own name to 127.0.0.1 still sends its own `Host`),
/// and a `POST /api/*` whose `Content-Type` isn't `application/json` (a cross-site `text/plain` or
/// form POST is a CORS "simple request" with no preflight; the cockpit always sends JSON, which
/// forces a preflight that we never approve). The port isn't checked: the router doesn't know it
/// and a rebinding page keeps the server's port anyway — the hostname is what matters.
async fn guard(req: Request, next: Next) -> Response {
    let host_ok = req
        .headers()
        .get(header::HOST)
        .and_then(|h| h.to_str().ok())
        .map_or(false, |h| matches!(host_name(h), "127.0.0.1" | "localhost" | "::1"));
    if !host_ok {
        return sent(StatusCode::FORBIDDEN, "text/plain", b"forbidden host".to_vec());
    }
    if req.method() == Method::POST && req.uri().path().starts_with("/api/") {
        let json = req
            .headers()
            .get(header::CONTENT_TYPE)
            .and_then(|v| v.to_str().ok())
            .map_or(false, |v| v.split(';').next().unwrap_or("").trim().eq_ignore_ascii_case("application/json"));
        if !json {
            return sent(StatusCode::UNSUPPORTED_MEDIA_TYPE, "text/plain", b"content-type must be application/json".to_vec());
        }
    }
    next.run(req).await
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

/// The engine's build stamp (git SHA + build time), lifted from the `version` field of the config
/// payload. A tiny, stable target for host/check-deploy.sh to curl and compare against HEAD, and
/// for the cockpit footer. `{}` if the config carries no version (older engine).
async fn version(State(c): State<Arc<Cockpit>>) -> Response {
    let v = c
        .config
        .lock()
        .unwrap()
        .get("version")
        .cloned()
        .unwrap_or_else(|| serde_json::json!({}));
    sent(StatusCode::OK, "application/json", serde_json::to_vec(&v).unwrap())
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
    c.enqueue(name, args);
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
        let mut last: Option<Arc<PreviewFrame>> = None;
        let mut tick = tokio::time::interval(Duration::from_millis(20));
        loop {
            tick.tick().await;
            let frame = rx.borrow().clone();
            if let Some(f) = frame {
                let is_new = match &last { Some(l) => !Arc::ptr_eq(l, &f), None => true };
                if is_new {
                    last = Some(f.clone());
                    let mut buf = Vec::with_capacity(f.jpeg.len() + 64);
                    buf.extend_from_slice(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ");
                    buf.extend_from_slice(f.jpeg.len().to_string().as_bytes());
                    buf.extend_from_slice(b"\r\n\r\n");
                    buf.extend_from_slice(&f.jpeg);
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

/// Standard-base64-encode `bytes`, appending to `out` (no padding omitted — RFC 4648). Used to carry
/// the preview JPEG inside a text SSE event; avoids a base64 crate dependency for this one use.
fn base64_append(out: &mut String, bytes: &[u8]) {
    const T: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut i = 0;
    while i + 3 <= bytes.len() {
        let n = ((bytes[i] as u32) << 16) | ((bytes[i + 1] as u32) << 8) | bytes[i + 2] as u32;
        out.push(T[(n >> 18 & 63) as usize] as char);
        out.push(T[(n >> 12 & 63) as usize] as char);
        out.push(T[(n >> 6 & 63) as usize] as char);
        out.push(T[(n & 63) as usize] as char);
        i += 3;
    }
    let rem = bytes.len() - i;
    if rem == 1 {
        let n = (bytes[i] as u32) << 16;
        out.push(T[(n >> 18 & 63) as usize] as char);
        out.push(T[(n >> 12 & 63) as usize] as char);
        out.push('=');
        out.push('=');
    } else if rem == 2 {
        let n = ((bytes[i] as u32) << 16) | ((bytes[i + 1] as u32) << 8);
        out.push(T[(n >> 18 & 63) as usize] as char);
        out.push(T[(n >> 12 & 63) as usize] as char);
        out.push(T[(n >> 6 & 63) as usize] as char);
        out.push('=');
    }
}

/// `GET /preview` — SSE carrying the newest camera frame AND its FaceMesh overlay together, so the
/// cockpit draws the picture and the landmark dots from one frame. Each event is
/// `data: {"img":"<base64 jpeg>","ov":<overlay json>}`. Polls the latest frame every 20 ms and
/// emits only on change (newest-wins, like `/camera`), so a slow client drops skipped frames rather
/// than queueing them.
async fn preview(State(c): State<Arc<Cockpit>>) -> Response {
    let rx = c.frame_tx.subscribe();
    let stream = async_stream::stream! {
        let mut last: Option<Arc<PreviewFrame>> = None;
        let mut tick = tokio::time::interval(Duration::from_millis(20));
        loop {
            tick.tick().await;
            let frame = rx.borrow().clone();
            if let Some(f) = frame {
                let is_new = match &last { Some(l) => !Arc::ptr_eq(l, &f), None => true };
                if is_new {
                    last = Some(f.clone());
                    let mut data = String::with_capacity(f.jpeg.len() * 4 / 3 + f.overlay.len() + 32);
                    data.push_str("data: {\"img\":\"");
                    base64_append(&mut data, &f.jpeg);
                    data.push_str("\",\"ov\":");
                    data.push_str(&f.overlay);
                    data.push_str("}\n\n");
                    yield Ok::<Bytes, std::io::Error>(Bytes::from(data));
                }
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};

    fn spawn(cockpit: Arc<Cockpit>) -> u16 {
        let l = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        l.set_nonblocking(true).unwrap();
        let port = l.local_addr().unwrap().port();
        std::thread::spawn(move || {
            let rt = tokio::runtime::Builder::new_multi_thread().enable_all().build().unwrap();
            rt.block_on(async move {
                let l = tokio::net::TcpListener::from_std(l).unwrap();
                axum::serve(l, router(cockpit)).await.unwrap();
            });
        });
        port
    }

    /// Send a raw HTTP/1.1 request and return the status code.
    fn status(port: u16, method: &str, path: &str, host: &str, ctype: Option<&str>, body: &str) -> u16 {
        let mut s = std::net::TcpStream::connect(("127.0.0.1", port)).unwrap();
        let ct = ctype.map(|c| format!("Content-Type: {c}\r\n")).unwrap_or_default();
        let req = format!(
            "{method} {path} HTTP/1.1\r\nHost: {host}\r\n{ct}Content-Length: {}\r\nConnection: close\r\n\r\n{body}",
            body.len()
        );
        s.write_all(req.as_bytes()).unwrap();
        let mut out = String::new();
        s.read_to_string(&mut out).unwrap();
        out.split_whitespace().nth(1).unwrap().parse().unwrap()
    }

    #[test]
    fn rejects_foreign_host_and_non_json_post() {
        let c = Cockpit::new(std::env::temp_dir(), serde_json::json!({}));
        let port = spawn(Arc::clone(&c));
        let own = format!("127.0.0.1:{port}");
        let json = Some("application/json");
        assert_eq!(status(port, "GET", "/api/get_config", &own, None, ""), 200);
        assert_eq!(status(port, "GET", "/api/get_config", &format!("localhost:{port}"), None, ""), 200);
        assert_eq!(status(port, "GET", "/api/get_config", &format!("evil.example:{port}"), None, ""), 403);
        assert_eq!(status(port, "POST", "/api/set_block", "evil.example", json, "[1]"), 403);
        assert_eq!(status(port, "POST", "/api/set_block", &own, Some("text/plain"), "[1]"), 415);
        assert_eq!(status(port, "POST", "/api/set_block", &own, None, "[1]"), 415);
        assert!(c.take_dispatched().is_empty());
        assert_eq!(status(port, "POST", "/api/set_block", &own, Some("application/json; charset=utf-8"), "[256]"), 200);
        assert_eq!(c.take_dispatched(), vec![("set_block".to_string(), serde_json::json!([256]))]);
        let big = format!("[\"{}\"]", "a".repeat(MAX_API_BODY + 1));
        assert_eq!(status(port, "POST", "/api/set_block", &own, json, &big), 413);
    }

    #[test]
    fn base64_matches_rfc4648_vectors() {
        let enc = |s: &str| {
            let mut out = String::new();
            base64_append(&mut out, s.as_bytes());
            out
        };
        assert_eq!(enc(""), "");
        assert_eq!(enc("f"), "Zg==");
        assert_eq!(enc("fo"), "Zm8=");
        assert_eq!(enc("foo"), "Zm9v");
        assert_eq!(enc("foob"), "Zm9vYg==");
        assert_eq!(enc("fooba"), "Zm9vYmE=");
        assert_eq!(enc("foobar"), "Zm9vYmFy");
        // round-trips every byte value (padding + all 6-bit groups exercised)
        let all: Vec<u8> = (0..=255u8).collect();
        let mut out = String::new();
        base64_append(&mut out, &all);
        assert_eq!(out.len(), (256 + 2) / 3 * 4); // 4 chars per 3 bytes, rounded up
        assert!(out.chars().all(|ch| ch.is_ascii_alphanumeric() || "+/=".contains(ch)));
    }

    #[test]
    fn dispatch_queue_is_bounded_and_coalesces() {
        let c = Cockpit::new(std::env::temp_dir(), serde_json::json!({}));
        for i in 0..5 {
            c.enqueue("set_block".into(), serde_json::json!([i]));
        }
        assert_eq!(c.take_dispatched(), vec![("set_block".to_string(), serde_json::json!([4]))]);
        // non-setters aren't coalesced, and the queue drops the oldest past the cap
        for i in 0..(MAX_DISPATCHED + 10) {
            c.enqueue("settings_commit".into(), serde_json::json!([i]));
        }
        let q = c.take_dispatched();
        assert_eq!(q.len(), MAX_DISPATCHED);
        assert_eq!(q[0].1, serde_json::json!([10]));
    }
}
