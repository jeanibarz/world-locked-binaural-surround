#!/usr/bin/env bash
# Deploy the current engine build to the owner's Windows install — the ONE blessed, in-place
# updater. Use this after ANY change to rust-engine/ or apps/web_cockpit/ (and before telling the
# owner to test), so the running Windows app is never stale. For a from-scratch bundle use
# host/bundle-engine.sh; this script only refreshes the exe + cockpit UI + the build stamp in an
# existing install, leaving packs/, hp_eq/, assets/, run-engine.bat, backups/ and traces/ untouched.
#
# Usage:  host/deploy-windows.sh [install_dir]
#   install_dir defaults to $WLBS_WIN_DIR, else /mnt/c/Users/ibarz/WLBS-Engine
#
# Verify afterwards with host/check-deploy.sh (also run by the Claude Stop hook).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
DST="${1:-${WLBS_WIN_DIR:-/mnt/c/Users/ibarz/WLBS-Engine}}"
TARGET_TRIPLE="x86_64-pc-windows-msvc"
: "${CARGO_TARGET_DIR:=$([ -d /mnt/d ] && echo /mnt/d/cargo-target || echo "$REPO/target")}"
export CARGO_TARGET_DIR

cd "$REPO"
SHA="$(git rev-parse HEAD)"
SHORT="$(git rev-parse --short HEAD)"
DIRTY=""; [ -n "$(git status --porcelain)" ] && DIRTY=" (dirty working tree)"

# Loud provenance guards — never block (the owner sometimes deploys an un-merged test build), but
# make it obvious what is being shipped.
echo "============================================================"
echo " Deploying engine $SHORT$DIRTY  ->  $DST"
git fetch origin --quiet 2>/dev/null || true
if ! git merge-base --is-ancestor HEAD origin/main 2>/dev/null; then
  echo " ⚠  HEAD is NOT on origin/main — deploying un-merged / un-pushed code."
fi
[ -n "$DIRTY" ] && echo " ⚠  Working tree has uncommitted changes — the build will be marked dirty."
echo "============================================================"

echo "building engine (cargo xwin, $TARGET_TRIPLE)…"
( cd "$REPO/rust-engine" && cargo xwin build --release --target "$TARGET_TRIPLE" >/dev/null )
EXE="$CARGO_TARGET_DIR/$TARGET_TRIPLE/release/wlbs_engine.exe"
[ -f "$EXE" ] || { echo "build produced no exe at $EXE" >&2; exit 1; }

[ -d "$DST" ] || { echo "install dir $DST not found — run host/bundle-engine.sh first for a fresh install" >&2; exit 1; }
mkdir -p "$DST/web_cockpit"
cp -f "$REPO/apps/web_cockpit/"* "$DST/web_cockpit/"
echo "cockpit UI updated"

# The exe is locked while the engine runs; stage it as wlbs_engine.new.exe (run-engine.bat applies
# it on next launch) and flag that a relaunch is needed. Otherwise replace it in place.
RELAUNCH=0
if cp -f "$EXE" "$DST/wlbs_engine.exe" 2>/dev/null; then
  rm -f "$DST/wlbs_engine.new.exe"
  echo "engine replaced in place (was not running)"
else
  cp -f "$EXE" "$DST/wlbs_engine.new.exe"
  RELAUNCH=1
  echo "engine STAGED as wlbs_engine.new.exe (it is running/locked) — relaunch to apply"
fi

printf '%s  deployed %s from %s%s\n' \
  "$SHA" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(git rev-parse --abbrev-ref HEAD)" "$DIRTY" \
  > "$DST/DEPLOYED_SHA"

echo "------------------------------------------------------------"
echo " Deployed $SHORT$DIRTY. Stamp: $DST/DEPLOYED_SHA"
if [ "$RELAUNCH" = 1 ]; then
  echo " ⚠  CLOSE the running engine window, then run run-engine.bat to apply the new exe."
else
  echo " Launch run-engine.bat (or it is already the live exe)."
fi
echo "------------------------------------------------------------"
