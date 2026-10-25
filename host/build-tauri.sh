#!/usr/bin/env bash
# Build the Tauri native shell (the standalone app window around the local cockpit server)
# and bundle it into a Windows installer.
#
# The Rust project lives in the repo (src-tauri/), but Windows cargo cannot build on the WSL 9P
# filesystem (incremental-lock + path-resolution errors), so we sync the SOURCE to a Windows build
# dir and build there with cargo.exe (which then sees a real C:\ cwd). target/ is kept between runs
# so only changed crates recompile.
#
# `cargo tauri build` (not plain `cargo build`) is what produces the installer: it compiles the
# release binary and then runs the bundler declared in tauri.conf.json (bundle.targets = ["nsis"]),
# emitting an NSIS *-setup.exe. The first bundle run downloads NSIS + the WebView2 bootstrapper
# (needs internet); later runs reuse them.
#
# NOTE ON PORTABILITY: the installer packages the native shell + WebView2 bootstrapper, but the
# shell still launches the Python DSP engine via the host `run.cmd` (hardcoded path in src/main.rs),
# which runs the WSL checkout over \\wsl.localhost\... So the installed app is self-contained as a
# *window*, but still depends on the owner's WSL Python stack + run.cmd at runtime. A fully portable
# distributable (bundling the engine) is a separate, larger effort and is NOT what this produces.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="/mnt/c/Users/ibarz/wlbs-build"

# `cargo tauri` comes from the tauri-cli crate; install it once if missing.
if ! cargo.exe tauri --version >/dev/null 2>&1; then
  echo "cargo-tauri not found — installing tauri-cli (one-time, compiles from source)..." >&2
  cargo.exe install tauri-cli --version "^2.0" --locked
fi

mkdir -p "$BUILD/src-tauri"
# sync source over (keep the existing target/ cache)
cp -r "$REPO/src-tauri/Cargo.toml" "$REPO/src-tauri/build.rs" "$REPO/src-tauri/tauri.conf.json" \
      "$REPO/src-tauri/src" "$REPO/src-tauri/icons" "$REPO/src-tauri/dist" "$BUILD/src-tauri/"
cd "$BUILD"
cargo.exe tauri build

echo
echo "shell exe : C:\\Users\\ibarz\\wlbs-build\\src-tauri\\target\\release\\wlbs-shell.exe"
echo "installer : C:\\Users\\ibarz\\wlbs-build\\src-tauri\\target\\release\\bundle\\nsis\\"
ls -1 "$BUILD/src-tauri/target/release/bundle/nsis/"*-setup.exe 2>/dev/null || true
