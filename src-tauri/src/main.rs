// Native shell for World-Locked Binaural Surround.
// Spawns the standalone Rust engine (hidden, fixed port, no window) and shows a single native
// window pointing at the local cockpit server. Closing the window kills the engine. The HTTP
// server stays reachable, so the same URL can be opened in Chrome for inspection.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::TcpStream;
use std::os::windows::process::CommandExt;
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::Manager;

const PORT: u16 = 8732;
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

struct Backend(Mutex<Option<Child>>);

/// The engine binary ships next to this shell's exe (the installer/bundle lays them out
/// side by side — see host/bundle-engine.sh). No hardcoded install path: resolve it from
/// our own location so the bundle runs wherever it is dropped.
fn engine_path() -> PathBuf {
    std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.join("wlbs_engine.exe")))
        .unwrap_or_else(|| PathBuf::from("wlbs_engine.exe"))
}

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            // Start the Rust engine hidden on the fixed port. It resolves its own assets
            // (ONNX models, BRIR packs, hp_eq, web_cockpit) relative to its exe and serves the
            // cockpit over HTTP; it does not open a browser of its own.
            let child = Command::new(engine_path())
                .args(["--port", &PORT.to_string()])
                .creation_flags(CREATE_NO_WINDOW)
                .spawn()
                .ok();
            app.manage(Backend(Mutex::new(child)));

            // the splash window is already up (from tauri.conf.json); once the server answers,
            // point it at the live cockpit URL.
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                let t0 = Instant::now();
                while TcpStream::connect(("127.0.0.1", PORT)).is_err()
                    && t0.elapsed() < Duration::from_secs(45)
                {
                    std::thread::sleep(Duration::from_millis(250));
                }
                if let Some(win) = handle.get_webview_window("main") {
                    if let Ok(url) = format!("http://127.0.0.1:{PORT}/index.html").parse() {
                        let _ = win.navigate(url);
                    }
                }
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                if let Some(state) = window.app_handle().try_state::<Backend>() {
                    if let Some(mut child) = state.0.lock().unwrap().take() {
                        let _ = child.kill();
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running the World-Locked Binaural Surround shell");
}
