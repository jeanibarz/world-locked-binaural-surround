#!/usr/bin/env bash
# Build the Tauri native shell (the standalone app window around the local cockpit server).
#
# The Rust project lives in the repo (src-tauri/), but Windows cargo cannot build on the WSL 9P
# filesystem (incremental-lock + path-resolution errors), so we sync the SOURCE to a Windows build
# dir and build there with cargo.exe (which then sees a real C:\ cwd). target/ is kept between runs
# so only changed crates recompile.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="/mnt/c/Users/ibarz/wlbs-build"
mkdir -p "$BUILD/src-tauri"
# sync source over (keep the existing target/ cache)
cp -r "$REPO/src-tauri/Cargo.toml" "$REPO/src-tauri/build.rs" "$REPO/src-tauri/tauri.conf.json" \
      "$REPO/src-tauri/src" "$REPO/src-tauri/icons" "$REPO/src-tauri/dist" "$BUILD/src-tauri/"
cd "$BUILD/src-tauri"
cargo.exe build --release
echo
echo "built: C:\\Users\\ibarz\\wlbs-build\\src-tauri\\target\\release\\wlbs-shell.exe"
