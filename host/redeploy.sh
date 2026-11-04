#!/usr/bin/env bash
# Redeploy PROPERLY — the one command that leaves the owner's RUNNING Windows app on the current
# code. It builds + installs the engine (host/deploy-windows.sh) AND, if the engine is running,
# restarts it so the live process actually runs the new build.
#
# Why a wrapper exists: deploying the files is not enough. `host/deploy-windows.sh` refreshes the
# exe on disk and stamps DEPLOYED_SHA = HEAD, so `host/check-deploy.sh` (the Stop hook) then reports
# "in sync" — but the *running* engine keeps the OLD build in memory until it is relaunched. On the
# WSL/DrvFs mount the running exe can even be overwritten in place without the live process
# changing, so "deployed" silently leaves the app stale. That gap is exactly the "forgot to redeploy
# properly" footgun. This script closes it: deploy, then relaunch the running engine, then confirm
# it came back.
#
# Use this (not bare deploy-windows.sh) after ANY change under rust-engine/ or apps/web_cockpit/,
# and before telling the owner to test.
#
# Usage:  host/redeploy.sh [install_dir]
#   install_dir defaults to $WLBS_WIN_DIR, else /mnt/c/Users/ibarz/WLBS-Engine (same as deploy).
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
DST="${1:-${WLBS_WIN_DIR:-/mnt/c/Users/ibarz/WLBS-Engine}}"
SHORT="$(cd "$REPO" && git rev-parse --short HEAD 2>/dev/null || echo '?')"

# 1) Build + install (fails loudly; do not touch the live engine if the build/copy failed).
"$REPO/host/deploy-windows.sh" "$DST" || { echo "redeploy: deploy step failed — live engine left untouched." >&2; exit 1; }

# 2) Make it live. Only possible on the Windows host (install dir + tasklist/taskkill present).
if [ ! -d "$DST" ]; then
  echo "redeploy: no install dir ($DST) — nothing to restart (not on the Windows host)."
  exit 0
fi
if ! command -v tasklist.exe >/dev/null 2>&1 || ! command -v taskkill.exe >/dev/null 2>&1; then
  echo "redeploy: tasklist/taskkill unavailable — cannot restart the engine from here."
  echo "          Relaunch it on the host (close the window, run run-engine.bat) to apply the new build."
  exit 0
fi

# CSV, no header: field 2 is the PID. Matches any running wlbs_engine.exe.
running_pids() {
  tasklist.exe /FI "IMAGENAME eq wlbs_engine.exe" /FO CSV /NH 2>/dev/null \
    | awk -F'","' 'tolower($0) ~ /wlbs_engine/ { gsub(/"/, "", $2); print $2 }'
}

PIDS="$(running_pids)"
if [ -z "$PIDS" ]; then
  echo "redeploy: engine is not running — the new build ($SHORT) will be used next time run-engine.bat starts."
  exit 0
fi

echo "redeploy: restarting the running engine so build $SHORT goes live (PIDs: $PIDS)…"
for p in $PIDS; do taskkill.exe /PID "$p" /F >/dev/null 2>&1 || true; done
sleep 2

# Relaunch detached in its own console. run-engine.bat applies a staged wlbs_engine.new.exe (del +
# rename) before starting, so this picks up the just-deployed build. The "start" may print an
# access-denied notice crossing the WSL boundary even when the launch succeeds — the PID re-check
# below is the real confirmation.
( cd "$DST" && cmd.exe /c 'start "" run-engine.bat' >/dev/null 2>&1 || true )
sleep 6

NEW="$(running_pids)"
# Success requires a FRESH process — a PID that was not running before the kill. A surviving old PID
# (e.g. taskkill was denied) must not be read as a successful relaunch: the old build would still be
# live.
FRESH=""
for np in $NEW; do
  is_old=0
  for op in $PIDS; do [ "$np" = "$op" ] && { is_old=1; break; }; done
  [ "$is_old" = 0 ] && FRESH="$FRESH $np"
done
FRESH="${FRESH# }"
if [ -n "$FRESH" ]; then
  echo "redeploy: engine relaunched (PID(s): $FRESH) — the live app now runs $SHORT."
  exit 0
fi
if [ -n "$NEW" ]; then
  echo "⚠  redeploy: the previous engine (PIDs: $NEW) is still running and no fresh process started —" >&2
  echo "   the kill was likely denied, so the OLD build is still live." >&2
else
  echo "⚠  redeploy: the engine did not come back up." >&2
fi
echo "   Fix on the host: close the engine window, then run $DST/run-engine.bat (it applies the staged build)." >&2
exit 1
