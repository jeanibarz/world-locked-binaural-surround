#!/usr/bin/env bash
# Is the deployed Windows engine current with the checkout? Prints a loud reminder and exits 2 when
# the install is stale *and* engine-relevant files changed since it was deployed; exits 0 (quiet)
# when in sync or when nothing engine-related changed. Fast and dependency-light (no network), so
# the Claude Stop hook (.claude/settings.json) can run it every turn without adding latency.
#
# Signals, in order:
#   1. a pending wlbs_engine.new.exe — a deploy staged while the engine ran, never applied,
#   2. the DEPLOYED_SHA stamp written by host/deploy-windows.sh / bundle-engine.sh vs HEAD.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
DST="${1:-${WLBS_WIN_DIR:-/mnt/c/Users/ibarz/WLBS-Engine}}"
ENGINE_PATHS=(rust-engine apps/web_cockpit rust-render-core-lib rust-conv-core rust-control-plane rust-audio-io rust-vision-client)
cd "$REPO" 2>/dev/null || exit 0
HEAD="$(git rev-parse HEAD 2>/dev/null)" || exit 0
SHORT="$(git rev-parse --short HEAD 2>/dev/null)"

# No install mounted (e.g. not on the Windows host) → nothing to check.
[ -d "$DST" ] || exit 0

stale() {
  echo "⚠  WINDOWS DEPLOY STALE — $1"
  echo "   The running Windows engine will NOT reflect the current code."
  echo "   Fix: host/deploy-windows.sh   then (on the host) close the engine window and run run-engine.bat"
  exit 2
}

# A deploy staged while the engine ran, never applied (our classic footgun).
[ -f "$DST/wlbs_engine.new.exe" ] && stale "an update is staged as wlbs_engine.new.exe but not applied (engine was running at deploy time)"

STAMP="$DST/DEPLOYED_SHA"
[ -f "$STAMP" ] || stale "no DEPLOYED_SHA stamp in $DST (never deployed via deploy-windows.sh)"
DEP_SHA="$(awk '{print $1; exit}' "$STAMP")"
[ "$DEP_SHA" = "$HEAD" ] && exit 0

# Stamp differs from HEAD: only nag if something the engine actually ships changed since the deploy.
if git cat-file -e "$DEP_SHA" 2>/dev/null; then
  git diff --quiet "$DEP_SHA" "$HEAD" -- "${ENGINE_PATHS[@]}" 2>/dev/null && exit 0
  stale "deployed ${DEP_SHA:0:7}, HEAD $SHORT — engine/UI changed since"
else
  stale "deployed $DEP_SHA is unknown to this checkout; HEAD is $SHORT"
fi
