# build_ring_driver.ps1 -- ONE reproducible build of the ring-bridge SYSVAD driver (run on the HOST).
#
# Problem this solves: the shipped driver is "stock SYSVAD + three source edits", but two of those
# edits were applied by hand (make_speaker_71.py, make_ring_bridge.py) and were NOT wired into any
# build script -- so a clean checkout + build silently produced a driver WITHOUT the ring. This
# orchestrator applies ALL three edits deterministically (idempotently), then builds, Win10-ports,
# and re-signs, so the signed package is reproducible from a stock source tree.
#
# The complete, authoritative edit set (RFC rfc-sysvad-real-loopback v5; journals 69, 79, 80):
#   1. make_speaker_71.py   -> EndpointsCommon\speakerwavtable.h + speakertoptable.h  (stereo -> 7.1)
#   2. common.cpp Win10 down-level lookaside compat block        (done inside apply_win10_compat.ps1)
#   3. make_ring_bridge.py  -> EndpointsCommon\minwavertstream.cpp (render->capture ring; real loopback)
# NOT applied: make_device_pull.py. The ring build is non-pull on purpose -- pull-driving the render
# stream stops audiodg filling its DMA (RFC v5 / journal 79). Do not add the pull edit here.
#
# Run order for a clean checkout (one-time EWDK + pkg staging via stage1..6 must already be done):
#   build_ring_driver.ps1     <- this (applies edits, builds, ports, signs)  => D:\kookr-vm\pkg10
#   install_ring_driver.ps1   <- then install (robust, no-reboot reload)
#
# Idempotent: re-running it re-applies cleanly (every edit is a no-op if already present) and rebuilds.

[CmdletBinding()]
param(
  [string]$Ws        = 'D:\kookr-vm',                         # build workspace (holds ewdk.iso, pkg, wds\...)
  [string]$Base      = '',                                    # sysvad source root (default: $Ws\wds\audio\sysvad)
  [string]$Patchers  = $PSScriptRoot,                         # dir holding make_speaker_71.py / make_ring_bridge.py
  [switch]$SkipPort                                           # stop after the Win10-loadable build (no pkg10/sign)
)
$ErrorActionPreference = 'Stop'
if (-not $Base) { $Base = "$Ws\wds\audio\sysvad" }
$Ec = "$Base\EndpointsCommon"

function Step($m) { Write-Host "`n=== $m ===" -ForegroundColor Cyan }
function Die($m)  { Write-Error $m; exit 1 }

# --- prerequisites (fail early with an actionable message, not a confusing build error) ---
Step "preflight"
if (-not (Test-Path $Ec))            { Die "sysvad source not found: $Ec  (clone the WDK sysvad sample there, or pass -Base)" }
if (-not (Test-Path "$Ws\ewdk.iso")) { Die "EWDK image missing: $Ws\ewdk.iso  (run stage6_ewdk_dl.ps1 first)" }
if (-not (Test-Path "$Ws\pkg\TabletAudioSample.sys")) {
  Die "package staging missing: $Ws\pkg  (the one-time full build/stage via stage1..6 must run before this)"
}
$cert = Get-ChildItem Cert:\CurrentUser\My | Where-Object { $_.Subject -eq 'CN=KookrTestDriver' } | Select-Object -First 1
if (-not $SkipPort -and -not $cert) { Die "signing cert CN=KookrTestDriver not in CurrentUser\My (run stage5_testsign.ps1)" }
$py = (Get-Command py -EA SilentlyContinue).Source; if (-not $py) { $py = (Get-Command python -EA SilentlyContinue).Source }
if (-not $py) { Die "python not found on PATH (needed to run the source patchers)" }
$s71  = Join-Path $Patchers 'make_speaker_71.py'
$sring = Join-Path $Patchers 'make_ring_bridge.py'
foreach ($p in @($s71, $sring)) { if (-not (Test-Path $p)) { Die "patcher missing: $p (pass -Patchers <dir of the spike patchers>)" } }
foreach ($h in @("$Ec\speakerwavtable.h", "$Ec\speakertoptable.h", "$Ec\minwavertstream.cpp")) {
  if (-not (Test-Path $h)) { Die "source file missing: $h" }
}
Write-Host "source=$Ec`npython=$py`npatchers=$Patchers"

# --- edit 1/3: 7.1 render endpoint (idempotent) ---
Step "edit 1/3 -- make_speaker_71.py (stereo -> 7.1)"
& $py $s71 $Ec
if ($LASTEXITCODE -ne 0) { Die "make_speaker_71.py failed" }

# --- edit 3/3: ring bridge (idempotent). edit 2/3 (common.cpp compat) is done by apply_win10_compat below ---
Step "edit 3/3 -- make_ring_bridge.py (render->capture ring)"
& $py $sring $Ec
if ($LASTEXITCODE -ne 0) { Die "make_ring_bridge.py failed" }

# fail loudly if the ring edit did not actually land in the source (anchors can drift between WDK versions)
$cpp = Get-Content "$Ec\minwavertstream.cpp" -Raw
if ($cpp -notmatch 'Plane T v2 ring bridge' -or $cpp -notmatch 'RingRead\(' -or $cpp -notmatch 'RingWrite\(') {
  Die "ring markers NOT present in minwavertstream.cpp after patching -- anchors drifted; inspect make_ring_bridge.py"
}
Write-Host "ring markers present in minwavertstream.cpp: OK"

# --- edit 2/3 + build: apply_win10_compat.ps1 inserts the common.cpp compat block, rebuilds, and
#     statically verifies every import resolves on the live Win10 kernel, then refreshes $Ws\pkg ---
Step "edit 2/3 + build -- apply_win10_compat.ps1 (common.cpp compat, EWDK rebuild, import verify)"
& (Join-Path $PSScriptRoot 'apply_win10_compat.ps1')
$res = "$Ws\compat_result.txt"
if (Test-Path $res) { Get-Content $res | ForEach-Object { Write-Host "  $_" } }
$sys = "$Base\TabletAudioSample\x64\Release\TabletAudioSample.sys"
if (-not (Test-Path $sys)) { Die "build produced no TabletAudioSample.sys (see $Ws\compat_build.log)" }
if ((Get-Content $res -Raw) -match 'STILL MISSING') { Die "build has imports unresolved on Win10 -- not loadable (see $res)" }
Write-Host "built sys sha256 = $((Get-FileHash $sys -Algorithm SHA256).Hash)"

if ($SkipPort) { Step "done (-SkipPort): Win10-loadable binary built; pkg10 not produced"; exit 0 }

# --- Win10 port + re-sign: host_port_win10.ps1 (22621 gate -> 19041, DriverVer bump, catalog, sign) ---
Step "port + sign -- host_port_win10.ps1  => $Ws\pkg10"
& (Join-Path $PSScriptRoot 'host_port_win10.ps1')
if ($LASTEXITCODE -ne 0) { Die "host_port_win10.ps1 failed" }

Step "BUILD COMPLETE"
Write-Host "signed package:  $Ws\pkg10\ComponentizedAudioSample.inf"
Write-Host "pkg10 sys sha256 = $((Get-FileHash "$Ws\pkg10\TabletAudioSample.sys" -Algorithm SHA256).Hash)"
Write-Host "next (elevated, test mode armed):  install_ring_driver.ps1"
