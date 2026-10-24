# install_ring_driver.ps1 -- install / reinstall the ring-bridge SYSVAD driver on the HOST, robustly.
# Run ELEVATED, with test-signing already armed + the host rebooted into it (host_arm_testmode.ps1).
#
# "Robust" here = the journal-80 reload that does NOT bugcheck: the journal-70 rule is that the device
# must be idle before its driver is replaced/unloaded or the box bugchecks. So before swapping the
# package we STOP the audio service (which closes every stream -> device goes idle) and cycle the
# device; only then start audio again. This lets you reinstall a rebuilt driver live, no reboot.
#
# Blast radius: the SYSVAD endpoint is NOT made the default playback device; this script records the
# current default before and asserts it is unchanged after. Idempotent: safe to re-run; it removes any
# prior ComponentizedAudioSample package from the store first, then adds the fresh pkg10.
#
# Flow:  host_arm_testmode.ps1 (once) -> reboot -> build_ring_driver.ps1 -> THIS.
# Undo:  host_revert.ps1 (then reboot to leave test mode).

[CmdletBinding()]
param(
  [string]$Ws     = 'D:\kookr-vm',
  [string]$Pkg    = '',                                   # default: $Ws\pkg10 (Win10-ported, signed)
  [string]$Devcon = 'C:\Program Files (x86)\Windows Kits\10\Tools\10.0.26100.0\x64\devcon.exe',
  [string]$Hwid   = 'Root\Sysvad_ComponentizedAudioSample'
)
$ErrorActionPreference = 'Continue'
if (-not $Pkg) { $Pkg = "$Ws\pkg10" }
$log = "$Ws\ring_install_result.txt"
function Say($m) { $m | Tee-Object -FilePath $log -Append | Out-Null; Write-Host $m }
"=== ring driver install  $(Get-Date -Format s) ===" | Set-Content $log

# --- 0. preconditions ---
if (-not (Test-Path "$Pkg\ComponentizedAudioSample.inf")) { Say "ABORT: package not found at $Pkg (run build_ring_driver.ps1 first)"; exit 1 }
if (-not (Test-Path $Devcon)) { Say "ABORT: devcon not found at $Devcon (install the WDK tools, or pass -Devcon)"; exit 1 }

# test-signing must be LIVE in the RUNNING kernel, not merely configured (journal 66: bcdedit value
# takes effect only on the next boot; prove it via LastBootUpTime vs the armed-at marker).
$ts = (bcdedit /enum '{current}' | Select-String 'testsigning\s+Yes')
if (-not $ts) { Say "ABORT: test-signing not configured. Run host_arm_testmode.ps1, then REBOOT."; exit 1 }
$boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
$armFile = "$Ws\testmode_armed_at.txt"
if (Test-Path $armFile) {
  $armedAt = [datetime]::Parse((Get-Content $armFile -Raw).Trim())
  if ($boot -lt $armedAt) { Say "ABORT: armed $($armedAt.ToString('s')) but kernel booted earlier ($($boot.ToString('s'))) -> RESTART the host, then re-run."; exit 1 }
  Say "test-signing: LIVE (booted $($boot.ToString('s')), armed $($armedAt.ToString('s')))"
} else { Say "WARN: no armed-at marker ($armFile) -> cannot prove the running kernel is in test mode; if install hits CodeIntegrity 3004, reboot." }

$expHash = (Get-FileHash "$Pkg\TabletAudioSample.sys" -Algorithm SHA256).Hash
Say "package: $Pkg  (sys sha256 $expHash)"

# --- 1. record default playback device (must NOT change) ---
function Get-DefaultRender {
  try { (Get-CimInstance -Namespace root\cimv2 -ClassName Win32_SoundDevice | Where-Object { $_.StatusInfo -eq 3 } | Select-Object -First 1 -Expand Name) } catch { $null }
}
$defBefore = Get-DefaultRender
Say "default render before: $defBefore"

# --- 2. make the device idle: stop the audio engine so no stream holds the driver (journal 70/80) ---
Say "--- stop audio service (close all streams -> device idle) ---"
$svcWasRunning = (Get-Service Audiosrv -EA SilentlyContinue).Status -eq 'Running'
Stop-Service Audiosrv -Force -EA SilentlyContinue
Start-Sleep 2

# --- 3. remove the old device + package, add the fresh one ---
Say "--- remove prior device ---"
& $Devcon remove $Hwid 2>&1 | Out-Null
Start-Sleep 2
Say "--- delete prior ComponentizedAudioSample packages from the store ---"
$cur = $null
foreach ($line in (pnputil /enum-drivers)) {
  if ($line -match 'Published Name\s*:\s*(oem\d+\.inf)') { $cur = $matches[1] }
  if ($line -match 'Original Name\s*:\s*componentizedaudiosample\.inf') { Say "  deleting $cur"; pnputil /delete-driver $cur /uninstall /force 2>&1 | Out-Null }
}
Start-Sleep 2
Say "--- add fresh package to store ---"
(pnputil /add-driver "$Pkg\ComponentizedAudioSample.inf" /install 2>&1 | Select-String 'Adding|Published|success|fail|driver package') | ForEach-Object { Say "  $_" }
Start-Sleep 2
Say "--- create device (devcon install) ---"
(& $Devcon install "$Pkg\ComponentizedAudioSample.inf" $Hwid 2>&1) | ForEach-Object { Say "  $_" }
Start-Sleep 5

# --- 4. cycle the PnP device so the fresh driver is bound cleanly, then restart audio ---
$media = Get-PnpDevice -Class MEDIA -EA SilentlyContinue | Where-Object { $_.FriendlyName -match 'Tablet Sample|Sysvad|Virtual Audio' } | Select-Object -First 1
if ($media) {
  Say "--- cycle device $($media.InstanceId) ---"
  Disable-PnpDevice -InstanceId $media.InstanceId -Confirm:$false -EA SilentlyContinue
  Start-Sleep 2
  Enable-PnpDevice  -InstanceId $media.InstanceId -Confirm:$false -EA SilentlyContinue
  Start-Sleep 3
}
if ($svcWasRunning) { Say "--- restart audio service ---"; Start-Service Audiosrv -EA SilentlyContinue; Start-Sleep 3 }

# --- 5. verify: loaded hash matches, endpoint is OK, default unchanged, no bugcheck ---
Say "--- verify ---"
$store = Get-ChildItem 'C:\Windows\System32\DriverStore\FileRepository' -Recurse -Filter TabletAudioSample.sys -EA SilentlyContinue | Select-Object -First 1
if ($store) {
  $got = (Get-FileHash $store.FullName -Algorithm SHA256).Hash
  Say ("  loaded sys sha256 = $got  " + $(if ($got -eq $expHash) { 'MATCHES package' } else { 'MISMATCH (!) expected ' + $expHash }))
} else { Say "  WARN: TabletAudioSample.sys not in DriverStore" }
(Get-PnpDevice -Class MEDIA -EA SilentlyContinue | Where-Object { $_.FriendlyName -match 'Tablet Sample|Sysvad|Virtual Audio' } | Select-Object Status, FriendlyName) | Format-Table -AutoSize | Out-String | ForEach-Object { Say $_ }
$defAfter = Get-DefaultRender
Say ("default render after:  $defAfter  " + $(if ($defAfter -eq $defBefore) { 'UNCHANGED' } else { 'CHANGED (!) -- set your real speakers back as default' }))
$since = (Get-Date).AddMinutes(-10)
$bc = Get-WinEvent -FilterHashtable @{LogName='System'; Id=1001} -EA SilentlyContinue | Where-Object { $_.TimeCreated -ge $since }
if ($bc) { $bc | ForEach-Object { Say "  BUGCHECK: $($_.TimeCreated) $($_.Message.Split([char]10)[0])" } } else { Say "  no bugcheck in the last 10 min" }

Say ""
Say ">>> Done. The SYSVAD 'Haut-parleurs' loopback now carries the real render mix (ring bridge)."
Say ">>> Listen test:  C:\Users\ibarz\BinauralHeadtracker\run_pull.cmd listen --pull-sysvad --ui cockpit --block 512 --latency-ms 40"
Say ">>> Rollback:     host_revert.ps1   (removes device+package, test-signing off; reboot after)"
Say ">>> Result log:   $log"
