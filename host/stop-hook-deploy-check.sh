#!/usr/bin/env bash
# Claude Code Stop-hook wrapper around host/check-deploy.sh. When the deployed Windows engine is
# stale (check-deploy exits 2), it blocks the stop once and feeds the reminder back to the model so
# a redeploy isn't forgotten; `stop_hook_active` breaks the loop so it nags at most once per turn.
# Reads the hook JSON on stdin, emits a Stop-hook decision on stdout. Always exits 0 (never errors
# the session). Requires jq.
set -uo pipefail

ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
payload="$(cat)"

# Already re-woken by a previous block on this turn → let the stop proceed (no infinite loop).
if printf '%s' "$payload" | jq -e '.stop_hook_active == true' >/dev/null 2>&1; then
  exit 0
fi

out="$("$ROOT/host/check-deploy.sh" 2>&1)"
[ "$?" -eq 2 ] || exit 0  # in sync (or no install) → nothing to say

# Stale: block the stop and hand the reminder to the model as the reason.
reason="$(printf '%s' "$out" | jq -Rs .)"
printf '{"decision":"block","reason":%s}\n' "$reason"
exit 0
