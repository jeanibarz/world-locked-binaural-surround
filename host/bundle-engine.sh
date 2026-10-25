#!/usr/bin/env bash
# Assemble the standalone zero-Python engine distributable and print its size.
#
# Produces a self-contained bundle that resolves all assets relative to the exe (no hardcoded
# paths — see rust-engine/src/assets.rs): drop it anywhere and run wlbs_engine.exe.
#
#   <bundle>/
#     wlbs_engine.exe        the engine (onnxruntime static-linked)
#     DirectML.dll           onnxruntime's DirectML EP (only runtime DLL dep)
#     assets/                facedet.onnx, facemesh.onnx, canonical_face_model.obj
#     packs/default/         the default BRIR pack (<ch>_L.wav / <ch>_R.wav)
#     web_cockpit/           the cockpit UI assets
#
# User-imported packs live under %APPDATA%\WLBS\packs (wlbs_engine --import-pack <dir>).
#
# Usage: host/bundle-engine.sh <out_dir> [onnx_dir] [canonical_obj] [default_pack_dir]
# Env:   ORT_LIB_DIR overrides where DirectML.dll is copied from (default: the ort cache).
set -euo pipefail

OUT="${1:?usage: bundle-engine.sh <out_dir> [onnx_dir] [canonical] [pack_dir]}"
ONNX="${2:-/mnt/c/Users/ibarz/facemesh_extract}"
CANON="${3:-/mnt/c/Users/ibarz/canonical_face_model.obj}"
PACK="${4:-/mnt/c/Program Files/EqualizerAPO/config/audeze-lcd-gx-binaural-surround/eqapo_config/baked_7p1_test}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${CARGO_TARGET_DIR:-$REPO/target}/x86_64-pc-windows-msvc/release/wlbs_engine.exe"

echo "building engine (cargo xwin, windows target)…"
( cd "$REPO/rust-engine" && cargo xwin build --release --target x86_64-pc-windows-msvc >/dev/null )

ORT_LIB_DIR="${ORT_LIB_DIR:-$(dirname "$(find "$HOME/.cache/ort.pyke.io" -iname DirectML.dll | head -1)")}"

rm -rf "$OUT"; mkdir -p "$OUT/assets" "$OUT/packs/default" "$OUT/web_cockpit"
cp "$TARGET" "$OUT/"
cp "$ORT_LIB_DIR/DirectML.dll" "$OUT/"
cp "$ONNX/facedet.onnx" "$ONNX/facemesh.onnx" "$OUT/assets/"
cp "$CANON" "$OUT/assets/"
cp "$PACK/"*.wav "$OUT/packs/default/"
cp -r "$REPO/apps/web_cockpit/"* "$OUT/web_cockpit/"

echo "bundle assembled at $OUT"
du -sh "$OUT"
du -sh "$OUT"/* 2>/dev/null || true
