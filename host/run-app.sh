#!/usr/bin/env bash
# Default launcher (Linux / macOS): start the NATIVE WINDOW (the Tauri shell binary).
#
# The shell starts the engine itself (hidden, on the fixed port 8731, --no-browser) and shows one
# native OS window. The cockpit HTTP server stays on http://127.0.0.1:8731 the whole time, so you can
# ALSO open that URL in any browser to inspect/debug the SAME live session, simultaneously with the
# window. Closing the window stops the app.
#
# Build the binary first with `host/build-tauri.sh`. Override its path with WLBS_SHELL_BIN.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${WLBS_SHELL_BIN:-$REPO/src-tauri/target/release/wlbs-shell}"
if [ ! -x "$BIN" ]; then
  echo "native shell not found at: $BIN" >&2
  echo "build it first:  host/build-tauri.sh   (set WLBS_SHELL_BIN to override the path)" >&2
  exit 1
fi
exec "$BIN"
