#!/usr/bin/env bash
# Prints the next iteration number, or "DONE" if the cap is reached.
WT=/home/jean/git/world-locked-binaural-surround-promo
python3 - "$WT/promo/loop/state.json" <<'PY'
import json,sys,os
p=sys.argv[1]
s=json.load(open(p)) if os.path.exists(p) else {"iteration":0,"max_iterations":50}
nxt=int(s.get("iteration",0))+1
print("DONE" if nxt>int(s.get("max_iterations",50)) else nxt)
PY
