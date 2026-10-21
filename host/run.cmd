@echo off
setlocal
REM ============================================================================
REM  binaural-headtracker — THE single host start script.
REM
REM  Replaces the old launch_live/cockpit/sweep/passthrough.py + run*.cmd zoo.
REM  One file, one argument = the mode:
REM
REM     run.cmd            (no arg -> listen)   daily world-lock, cockpit UI
REM     run.cmd listen                          same as above
REM     run.cmd test                            + HRIR-set blind A/B & loc test
REM     run.cmd calibrate                       recenter / axis-sign check
REM     run.cmd latency                         latency sweep (headless dev)
REM
REM  Everything after the mode is forwarded to the app, e.g.:
REM     run.cmd listen --method nearest --block 1024
REM
REM  It runs the WSL *main* checkout's code live over a \\wsl.localhost UNC path
REM  (dev worktrees get deleted after merge, so we don't chase them) — editing
REM  the Python needs no redeploy. Needs host Python + deps:
REM     py -3.12 -m pip install dearpygui sounddevice opencv-python mediapipe
REM
REM  listen/test loop on exit code 97 (the in-app Reload on Enter): a fresh
REM  process re-reads the WSL code and re-opens the audio devices cleanly.
REM ============================================================================

set "MODE=%~1"
if "%MODE%"=="" set "MODE=listen"
shift

REM %* ignores shift in cmd, so collect the forwarded flags by hand.
set "EXTRA="
:collect
if "%~1"=="" goto dispatch
set "EXTRA=%EXTRA% %1"
shift
goto collect
:dispatch

set "ROOT=\\wsl.localhost\Ubuntu-22.04\home\jean\git\binaural-headtracker"
set "LAUNCH=%ROOT%\apps\launch.py"

REM --- host assets (kept next to this .cmd) + device config -------------------
set "MODEL=%~dp0face_landmarker.task"
set "SOURCE=%~dp0dry_snare.wav"
set "HRIRSETS=%~dp0hrir_sets"
set "BRIR=C:\Program Files\EqualizerAPO\config\audeze-lcd-gx-binaural-surround\eqapo_config\baked_7p1_test"
set "OUTEQ=C:\Program Files\EqualizerAPO\config\audeze-lcd-gx-binaural-surround\eqapo_config\baked_7p1_rev26_final.txt"
REM 2048 gives timealign + the cockpit headroom; drop to 1024/512 for lower
REM latency if you don't get dropouts (press S in-app for the live load meter).
set "BLOCK=2048"

set "COMMON=--model "%MODEL%" --brir-dir "%BRIR%" --no-dialog --camera 0 --audio-device FiiO --lfe-mode brir --output-eq "%OUTEQ%" --source "%SOURCE%" --method timealign --rust --gain 1.0 --block %BLOCK% --latency-ms 20 --predict"

if /I "%MODE%"=="listen" goto passthrough
if /I "%MODE%"=="test"   goto passthrough
if /I "%MODE%"=="calibrate" goto calibrate
if /I "%MODE%"=="latency"   goto latency
echo [run] unknown mode "%MODE%"  (use: listen ^| test ^| calibrate ^| latency)
goto end

:passthrough
:loop
if /I "%MODE%"=="test" (
  py -3.12 "%LAUNCH%" test %COMMON% --hrir-sets-dir "%HRIRSETS%" %EXTRA%
) else (
  py -3.12 "%LAUNCH%" listen %COMMON% %EXTRA%
)
if %errorlevel%==97 goto loop
goto end

:calibrate
py -3.12 "%LAUNCH%" calibrate --model "%MODEL%" --no-dialog --camera 0 %EXTRA%
goto end

:latency
REM headless; synthetic pose + sine on the REAL FiiO output measures the true device-buffer
REM term. Close anything else using the FiiO (incl. the passthrough) first, or exclusive rows
REM FAIL (the sweep continues). Writes latency_sweep_results.json next to this .cmd.
py -3.12 "%LAUNCH%" latency --brir-dir "%BRIR%" ^
  --output device --audio-device FiiO --rust ^
  --methods timealign --blocks 256 512 1024 --latencies 20 ^
  --exclusive-modes shared exclusive --warmup 3 --seconds 8 ^
  --out "%~dp0latency_sweep_results.json" %EXTRA%
goto end

:end
pause
