# 89 — "Redeploy properly": deploy must also restart the running engine

## Question and context

The owner reported, with some frustration, that changes "never seem to be live" and asked for the
app to be redeployed *properly*, every time, automatically. Investigation of the deploy path found a
real blind spot, not just forgetfulness.

`host/deploy-windows.sh` refreshes the exe + `web_cockpit/` on disk and stamps
`DEPLOYED_SHA = HEAD`. `host/check-deploy.sh` (wired as a Claude Stop hook) compares `DEPLOYED_SHA`
to `HEAD` and, finding them equal, reports "in sync" and stays quiet. But a *running* engine keeps
the old build in memory until it is relaunched — and on the WSL/DrvFs mount the running exe is
frequently overwritten **in place** rather than failing the copy (so it is not staged as
`wlbs_engine.new.exe` either). The net effect: the files are current, `DEPLOYED_SHA` matches `HEAD`,
the Stop hook is silent — and the live app is still running the old build. "Deployed" looked done
while the running process was stale. (Observed directly: deploying `055ed30` left the engine process
on the previously-running `ad7def0` in memory; only a restart made the new build live.)

## Hypothesis

"Redeploy" has to mean "the running app is now on the new build", not "the files on disk are new".
That requires relaunching the running engine as part of the deploy, and keying the exe-placement
decision on whether the engine is *running*, not on whether the file copy happens to be allowed.

## Method and implementation

- **`host/redeploy.sh` (new)** — the "redeploy properly" entry point. Runs
  `host/deploy-windows.sh`, then, on the Windows host, if `wlbs_engine.exe` is running, `taskkill`s
  it and relaunches `run-engine.bat` detached (which applies any staged `wlbs_engine.new.exe` and
  starts the new build), then re-checks the PID to confirm the engine came back. No-ops cleanly off
  the Windows host (no install dir, or no `tasklist`/`taskkill`). PID parsing verified live against a
  running engine; the kill+relaunch mechanism was validated by hand before scripting.
- **`host/deploy-windows.sh`** — now keys exe placement on whether the engine is running
  (`tasklist.exe`), not on whether the in-place copy succeeds. If it is running, it always *stages*
  `wlbs_engine.new.exe` (so `check-deploy.sh`'s existing staged-but-not-applied detection flags it as
  owed until relaunch) instead of silently overwriting the mapped exe in place. Only replaces in
  place when the engine is not running.
- **`CLAUDE.md`/`AGENTS.md`** — the NON-NEGOTIABLE deploy rule now names `host/redeploy.sh` as the
  standard post-change action, spells out that deploying files is not enough (the process must be
  relaunched), and says to do it automatically so the owner never has to ask.

## Result and verification

- Redeployed `055ed30` live and confirmed via the cockpit: `/api/get_config` `version.short =
  055ed30`, `max_gain_db` present, served `cockpit.js` carries the new feature, audio levels
  flowing — i.e. the restart (not just the file copy) is what made it live.
- `bash -n` clean on both scripts; `redeploy.sh` executable; live PID detection returns the running
  engine's PID correctly.

## Interpretation

The guardrail chain (deploy script + stamp + check + Stop hook) was sound except for one unproven
assumption: that a current `DEPLOYED_SHA` implies a current *running* process. On WSL it does not.
Making restart part of redeploy — and making a bare deploy honestly flag "running, not yet
relaunched" — removes the last place staleness could hide.

## Learnings

- "Deployed" for a long-running compiled service means the live **process** runs the new build, not
  that the new binary sits on disk. Verify the process, and prefer a one-command deploy+restart.
- A staleness check keyed on a file/stamp can be defeated by an in-memory process; on WSL/DrvFs an
  in-place overwrite of a running exe is the trap.

## Decision / next

Shipped. `host/redeploy.sh` is the standard. Possible follow-up: have `run-engine.bat` stamp a
`RUNNING_SHA` on launch so `check-deploy.sh` can compare the *running* build to `HEAD` directly
(rather than inferring from the staged-exe marker), closing the gap even for a bare deploy without a
restart.
