#!/usr/bin/env bash
# Create (or update) the hourly Kookr schedule that runs the promo-video-iterate playbook.
# Stores the schedule id in promo/loop/state.json so the playbook can self-disable at the cap.
# Env: CRON (default hourly), ENABLED (default false — create disabled, enable after a test run).
set -euo pipefail
API="${KOOKR_API_BASE_URL:-http://127.0.0.1:4800}"
WT=/home/jean/git/world-locked-binaural-surround-promo
NAME="Promo Explainer Hourly Improve"
PLAYBOOK_PATH="promo-video-iterate.md"
CRON="${CRON:-0 * * * *}"
ENABLED="${ENABLED:-false}"

[ -f "$WT/.kookr/playbooks/$PLAYBOOK_PATH" ] || { echo "ERROR: playbook missing at $WT/.kookr/playbooks/$PLAYBOOK_PATH"; exit 1; }
curl -sS "$API/api/schedules" >/dev/null || { echo "ERROR: Kookr server not reachable at $API"; exit 1; }

BODY=$(NAME="$NAME" CRON="$CRON" WT="$WT" ENABLED="$ENABLED" PB="$PLAYBOOK_PATH" python3 - <<'PY'
import json,os
print(json.dumps({
  "name":os.environ["NAME"],"cron":os.environ["CRON"],"cwd":os.environ["WT"],
  "enabled":os.environ["ENABLED"]=="true",
  "playbook":{"path":os.environ["PB"],"parameters":{}},
}))
PY
)
# find existing by playbook path or name
EXIST=$(curl -sS "$API/api/schedules" | WT="$WT" PB="$PLAYBOOK_PATH" NAME="$NAME" python3 -c '
import json,os,sys
raw=json.load(sys.stdin); s=raw if isinstance(raw,list) else (raw.get("schedules") or raw.get("items") or [])
pb=os.environ["PB"]; nm=os.environ["NAME"]; found=""
for x in s:
  if not isinstance(x,dict): continue
  p=(x.get("playbook") or {}); pp=p.get("path") if isinstance(p,dict) else None
  if pp==pb or x.get("name")==nm: found=x.get("id",""); break
print(found)')

if [ -n "$EXIST" ]; then
  echo "Updating schedule $EXIST ..."
  RESP=$(curl -sS -X PATCH "$API/api/schedules/$EXIST" -H 'content-type: application/json' -d "$BODY")
  SID="$EXIST"
else
  echo "Creating schedule ..."
  RESP=$(curl -sS -X POST "$API/api/schedules" -H 'content-type: application/json' -d "$BODY")
  SID=$(printf '%s' "$RESP" | python3 -c 'import json,sys
try:
  r=json.load(sys.stdin); r=r.get("schedule",r) if isinstance(r,dict) else r
  print(r.get("id","") if isinstance(r,dict) else "")
except Exception: print("")')
fi
echo "schedule id: $SID"
# store id in state.json
python3 - "$WT/promo/loop/state.json" "$SID" <<'PY'
import json,sys,os
p,sid=sys.argv[1],sys.argv[2]
s=json.load(open(p)) if os.path.exists(p) else {}
s["schedule_id"]=sid or s.get("schedule_id")
json.dump(s,open(p,"w"),indent=2)
PY
echo "stored schedule_id in state.json"
kookr schedule list 2>/dev/null | grep -i "Promo Explainer" || true
