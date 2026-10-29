#!/usr/bin/env bash
# Mechanical close-out for one iteration (the fragile invariants, kept out of agent judgment):
#   save versioned video -> Windows, update durable state + log, local commit + tag.
# Usage: iterate_finish.sh <N> "<short summary of what changed this version>"
set -euo pipefail
WT=/home/jean/git/world-locked-binaural-surround-promo
B=/home/jean/promo-loop/build
WIN=/mnt/c/Users/ibarz/Videos
N="$1"; SUMMARY="${2:-iteration}"; NN=$(printf "%02d" "$N")

test -s "$B/explainer.mp4" || { echo "ERROR: no $B/explainer.mp4 to publish"; exit 1; }
cp -f "$B/explainer.mp4" "$WIN/world-locked-explainer-v$NN.mp4"
cp -f "$B/explainer.mp4" "$B/explainer-current.mp4"

python3 - "$WT/promo/loop/state.json" "$N" "$SUMMARY" <<'PY'
import json,sys,os,datetime
p,N,summ=sys.argv[1],int(sys.argv[2]),sys.argv[3]
s=json.load(open(p)) if os.path.exists(p) else {"iteration":0,"max_iterations":50,"history":[]}
s["iteration"]=N
s.setdefault("history",[]).append({
  "n":N,"when":datetime.datetime.now().isoformat(timespec="seconds"),
  "summary":summ,"video":f"world-locked-explainer-v{N:02d}.mp4"})
json.dump(s,open(p,"w"),indent=2)
PY

printf -- "| v%s | explainer-v%s | %s |\n" "$NN" "$NN" "$SUMMARY" >> "$WT/promo/loop/ITERATIONS.md"

cd "$WT"
git add promo/scripts promo/loop promo/notes .kookr/playbooks 2>/dev/null || true
git -c user.name="Jean IBARZ" commit -q -m "promo explainer: iteration $NN - $SUMMARY

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>" || echo "(nothing new to commit)"
git tag -f "explainer-v$NN" >/dev/null 2>&1 || true
git push -q origin promo/explainer-video 2>/dev/null || echo "(push skipped/failed - local history intact)"
git push -q --tags origin 2>/dev/null || true
echo "SAVED v$NN -> $WIN/world-locked-explainer-v$NN.mp4   (committed + tagged explainer-v$NN)"
