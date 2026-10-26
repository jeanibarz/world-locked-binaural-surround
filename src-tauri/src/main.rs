// Native shell for World-Locked Binaural Surround.
// Spawns the engine (hidden, fixed port, no browser) and shows a single native window pointing at
// the local cockpit server. Closing the window stops the engine. The HTTP server stays reachable,
// so the same URL can be opened in Chrome for inspection.
//
// Cross-platform: the window runs on Windows (WebView2), macOS (WKWebView) and Linux (WebKitGTK).
// Only the *engine launch* differs per OS, and it is resolved from the environment (no hardcoded
// path), so the same binary works on any host:
//
//   WLBS_ENGINE_CMD   full command line for the engine, run through the platform shell. The engine
//                     MUST serve the cockpit on $WLBS_PORT (exported for it) and not open a browser.
//                     When set, it wins on every OS.
//   WLBS_RUN_CMD      (Windows) path to the host `run.cmd`. Default: the owner's install location,
//                     kept so the existing Windows deployment is unchanged.
//   WLBS_LAUNCH       (Unix) the launcher prefix. Default: `python3 apps/launch.py`.
//   WLBS_HOME         (Unix) working directory the launcher resolves against (e.g. the repo root).
#![cfg_attr(
    all(not(debug_assertions), target_os = "windows"),
    windows_subsystem = "windows"
)]

use std::net::TcpStream;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::Manager;

const PORT: u16 = 8731;

struct Backend(Mutex<Option<Child>>);

/// Start the engine, hidden, on `port`, with no auto-browser. Returns the child if it spawned.
/// Platform-specific only in how the process is launched; both honour `WLBS_ENGINE_CMD`.
#[cfg(windows)]
fn spawn_engine(port: u16) -> Option<Child> {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000; // don't flash a console window

    let port = port.to_string();
    if let Ok(cmd) = std::env::var("WLBS_ENGINE_CMD") {
        return Command::new("cmd")
            .args(["/c", &cmd])
            .env("WLBS_PORT", &port)
            .creation_flags(CREATE_NO_WINDOW)
            .spawn()
            .ok();
    }
    // Default: the host run.cmd. Overridable via WLBS_RUN_CMD; the literal default preserves the
    // owner's existing Windows install so this change does not regress it.
    let run_cmd = std::env::var("WLBS_RUN_CMD")
        .unwrap_or_else(|_| r"C:\Users\ibarz\BinauralHeadtracker\run.cmd".to_string());
    Command::new("cmd")
        .args(["/c", &run_cmd, "listen", "--port", &port, "--no-browser"])
        .env("WLBS_PORT", &port)
        .creation_flags(CREATE_NO_WINDOW)
        .spawn()
        .ok()
}

#[cfg(unix)]
fn spawn_engine(port: u16) -> Option<Child> {
    let port = port.to_string();
    let shell_cmd = match std::env::var("WLBS_ENGINE_CMD") {
        Ok(cmd) => cmd,
        // Default: the repo's Python launcher. WLBS_LAUNCH overrides the prefix; WLBS_HOME sets the
        // working dir so `apps/launch.py` resolves.
        Err(_) => {
            let launch = std::env::var("WLBS_LAUNCH")
                .unwrap_or_else(|_| "python3 apps/launch.py".to_string());
            format!("{launch} listen --port {port} --no-browser")
        }
    };
    let mut cmd = Command::new("sh");
    cmd.args(["-c", &shell_cmd]).env("WLBS_PORT", &port);
    if let Ok(home) = std::env::var("WLBS_HOME") {
        cmd.current_dir(home);
    }
    cmd.spawn().ok()
}

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            // start the engine hidden on the fixed port, no auto-browser
            app.manage(Backend(Mutex::new(spawn_engine(PORT))));

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
