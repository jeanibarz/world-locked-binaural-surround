#!/usr/bin/env bash
# Build the Tauri native shell (the standalone app window around the local cockpit server).
#
# Cross-platform. Two modes:
#   ./build-tauri.sh            native build for THIS host (Linux / macOS, or Windows cargo directly)
#   ./build-tauri.sh windows    build the Windows .exe from WSL (sync to a Windows FS, use cargo.exe)
#
# Why the `windows` mode exists: Windows cargo cannot build on the WSL 9P filesystem (incremental-lock
# + path-resolution errors), so it syncs the SOURCE to a Windows build dir and builds there with
# cargo.exe (which then sees a real C:\ cwd). target/ is kept between runs so only changed crates
# recompile. The native mode just builds in place.
#
# Linux build deps (Tauri / WebKitGTK), Debian/Ubuntu names:
#   libwebkit2gtk-4.1-dev libgtk-3-dev librsvg2-dev libayatana-appindicator3-dev build-essential
# macOS needs only the Xcode command-line tools.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
MODE="${1:-native}"

if [ "$MODE" = "windows" ]; then
  BUILD="/mnt/c/Users/ibarz/wlbs-build"
  mkdir -p "$BUILD/src-tauri"
  # sync source over (keep the existing target/ cache)
  cp -r "$REPO/src-tauri/Cargo.toml" "$REPO/src-tauri/build.rs" "$REPO/src-tauri/tauri.conf.json" \
        "$REPO/src-tauri/src" "$REPO/src-tauri/icons" "$REPO/src-tauri/dist" "$BUILD/src-tauri/"
  cd "$BUILD/src-tauri"
  cargo.exe build --release
  echo
  echo "built: C:\\Users\\ibarz\\wlbs-build\\src-tauri\\target\\release\\wlbs-shell.exe"
else
  cd "$REPO/src-tauri"
  cargo build --release
  echo
  echo "built: $REPO/src-tauri/target/release/wlbs-shell"
fi
