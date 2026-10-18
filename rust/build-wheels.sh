#!/usr/bin/env bash
# Build distributable wheels for the native render kernel (B8.3).
#
# The kernel is built **abi3** (stable ABI, floor CPython 3.10), so ONE wheel per
# platform/arch runs on Python 3.10+ including the newest — you do not rebuild per Python
# version. A wheel has no OS-specific code, so colleagues `pip install` the wheel for their
# platform; without one, the app falls back to the pure-Python renderer (slower, but works).
#
# Usage:  rust/build-wheels.sh [linux|windows|all]      (default: linux)
# Output: wheels land in ./dist/ (repo root).
#
# Cross-platform coverage from one Linux/WSL box:
#   - linux x86_64 : native (this script, no extra setup).
#   - windows x86_64: cross-compiled via cargo-xwin (prereqs below) — no Windows needed.
#   - macOS        : NOT cross-buildable from Linux (needs Apple's SDK + realistically a Mac).
#                    On the Mac: `pip install maturin` then
#                    `maturin build --release -m rust/Cargo.toml -o dist`  (one command).
#
# Windows cross-build prerequisites (one-time, on the Linux/WSL host):
#   rustup target add x86_64-pc-windows-msvc
#   cargo install cargo-xwin                      # downloads the MS CRT/SDK automatically
#   sudo apt-get install -y clang lld llvm        # LLVM CLI tools cargo-xwin links with
#   ln -sf "$(command -v clang)" ~/.cargo/bin/clang-cl   # Ubuntu ships no clang-cl symlink
# Note: cargo-xwin cannot write to a Windows drive mount, so this script forces a native
# CARGO_TARGET_DIR under $HOME/.cache (DrvFs mounts give "Permission denied" on llvm-dlltool).

set -euo pipefail

TARGET="${1:-linux}"
HERE="$(cd "$(dirname "$0")" && pwd)"          # rust/
ROOT="$(cd "$HERE/.." && pwd)"                 # repo root
OUT="$ROOT/dist"
MANIFEST="$HERE/Cargo.toml"
mkdir -p "$OUT"

build_linux() {
  echo ">> building linux x86_64 abi3 wheel"
  CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$HOME/.cache/bt-lintarget}" \
    maturin build --release -m "$MANIFEST" -o "$OUT"
}

build_windows() {
  echo ">> cross-building windows x86_64 abi3 wheel (cargo-xwin)"
  # A native (non-DrvFs) target dir: llvm-dlltool fails with EACCES on /mnt/* mounts.
  CARGO_TARGET_DIR="$HOME/.cache/bt-wintarget" \
    maturin build --release --target x86_64-pc-windows-msvc -m "$MANIFEST" -o "$OUT"
}

case "$TARGET" in
  linux)   build_linux ;;
  windows) build_windows ;;
  all)     build_linux; build_windows ;;
  *) echo "usage: $0 [linux|windows|all]" >&2; exit 2 ;;
esac

echo ">> wheels in $OUT:"
ls -1 "$OUT"/*.whl
